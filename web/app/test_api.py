"""Smoke test for the API with a fake converter. Run: python test_api.py"""
import logging
import os
import struct
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).parent
DATA = Path(tempfile.mkdtemp(prefix="stl2step-test-"))
FAKE = HERE / "tests" / "fake_stl2step.py"
if os.name == "nt":
    launcher = DATA / "stl2step.cmd"
    launcher.write_text(f'@"{sys.executable}" "{FAKE}" %*\n')
else:
    launcher = DATA / "stl2step"
    launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE}" "$@"\n')
    launcher.chmod(0o755)
os.environ.update({"STL2STEP_BIN": str(launcher), "DATA_DIR": str(DATA)})

from fastapi.testclient import TestClient  # noqa: E402

logging.getLogger("httpx").setLevel(logging.WARNING)

import main  # noqa: E402

STL = b"\0" * 80 + struct.pack("<I", 2) + b"\0" * 100
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 32


def wait_done(c, jid, timeout=10):
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = c.get(f"/api/jobs/{jid}").json()
        if j["status"] not in ("queued", "running"):
            return j
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def run():
    with TestClient(main.app) as c:
        assert c.get("/api/health").json()["converter"] is True

        r = c.post("/api/jobs", files={"files": ("notes.txt", b"hello", "text/plain")})
        assert r.status_code == 400, r.text
        r = c.post("/api/jobs", files={"files": ("bad.stl", b"garbage garbage", "model/stl")})
        assert r.status_code == 400, r.text
        r = c.post("/api/jobs", files={"files": ("part.stl", STL, "model/stl")}, data={"engine": "nope"})
        assert r.status_code == 400, r.text

        r = c.post("/api/jobs", files={"files": ("handle-lock.stl", STL, "model/stl")},
                   data={"engine": "trueform", "schema": "AP214"})
        assert r.status_code == 202, r.text
        jid = r.json()["jobs"][0]
        j = wait_done(c, jid)
        assert j["status"] == "done", j
        assert j["result"]["smoothCylinders"] == 15
        assert j["preview"] is None and j["thumb"] is False

        assert c.get(f"/api/jobs/{jid}/input.stl").content == STL
        assert c.get(f"/api/jobs/{jid}/step").headers["content-disposition"].endswith('"handle-lock.step"')

        r = c.get(f"/api/jobs/{jid}/preview.stl")
        assert r.status_code == 200 and r.headers["cache-control"] == "no-store", r.text
        assert len(c.get(f"/api/jobs/{jid}/preview.edges").content) == 12 * 24
        assert c.get(f"/api/jobs/{jid}").json()["preview"]["faces"] == 35

        assert c.post(f"/api/jobs/{jid}/thumb", content=b"GIF89a").status_code == 400
        assert c.post(f"/api/jobs/{jid}/thumb", content=PNG).status_code == 204
        assert c.get(f"/api/jobs/{jid}/thumb").content == PNG
        assert c.get(f"/api/jobs/{jid}").json()["thumb"] is True

        r = c.post(f"/api/jobs/{jid}/reconvert", data={"engine": "verbatim"})
        assert r.status_code == 202, r.text
        j2 = wait_done(c, r.json()["jobs"][0])
        assert j2["name"] == "handle-lock.stl" and j2["options"]["engine"] == "verbatim"
        assert "smoothCylinders" not in j2["result"]

        r = c.post("/api/convert", files={"file": ("sync.stl", STL, "model/stl")}, data={"engine": "verbatim"})
        assert r.status_code == 200 and "X-Stl2step-Result" in r.headers, r.text
        assert r.content.startswith(b"ISO-10303-21")

        assert c.delete(f"/api/jobs/{jid}").status_code == 204
        assert c.get(f"/api/jobs/{jid}").status_code == 404
        assert len(c.get("/api/jobs").json()["jobs"]) == 2

        h = c.get("/").headers
        assert "script-src 'self'" in h["content-security-policy"]
    print("ok")


if __name__ == "__main__":
    run()
