"""Smoke test for the API with fake converter/importer. Run: python test_api.py"""
import logging
import os
import struct
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).parent
DATA = Path(tempfile.mkdtemp(prefix="stl2step-test-"))


def shim(name, script):
    if os.name == "nt":
        launcher = DATA / f"{name}.cmd"
        launcher.write_text(f'@"{sys.executable}" "{script}" %*\n')
    else:
        launcher = DATA / name
        launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n')
        launcher.chmod(0o755)
    return str(launcher)


os.environ.update({"STL2STEP_BIN": shim("stl2step", HERE / "tests" / "fake_stl2step.py"),
                   "ASSIMP_BIN": shim("assimp", HERE / "tests" / "fake_assimp.py"),
                   "DATA_DIR": str(DATA)})

from fastapi.testclient import TestClient  # noqa: E402

logging.getLogger("httpx").setLevel(logging.WARNING)

import main  # noqa: E402

STL = b"\0" * 80 + struct.pack("<I", 2) + b"\0" * 100
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 32
OBJ = (b"# cube\n" + b"".join(f"v {x} {y} {z}\n".encode() for x in (0, 1) for y in (0, 1) for z in (0, 1))
       + b"f 1 2 4 3\nf 5 7 8 6\nf 1 5 6 2\nf 3 4 8 7\nf 1 3 7 5\nf 2 6 8 4\n")


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
        h = c.get("/api/health").json()
        assert h["converter"] is True and h["importer"] is True and "obj" in h["formats"]

        r = c.post("/api/jobs", files={"files": ("notes.txt", b"hello world, nothing here", "text/plain")})
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

        # non-STL meshes go through the importer first
        r = c.post("/api/jobs", files={"files": ("cube.OBJ", OBJ, "text/plain")}, data={"engine": "verbatim"})
        assert r.status_code == 202, r.text
        jo = wait_done(c, r.json()["jobs"][0])
        assert jo["status"] == "done" and jo["name"] == "cube.obj", jo
        stl = c.get(f"/api/jobs/{jo['id']}/input.stl").content
        assert struct.unpack_from("<I", stl, 80)[0] == 12
        r = c.post("/api/jobs", files={"files": ("mislabelled.stl", OBJ, "model/stl")})
        assert r.status_code == 202 and c.get(f"/api/jobs/{r.json()['jobs'][0]}").json()["name"] == "mislabelled.obj"
        r = c.post("/api/jobs", files={"files": ("x.fbx", b"Kaydara FBX Binary  \0" + b"\0" * 40, "x")})
        assert r.status_code == 202, r.text
        jf = wait_done(c, r.json()["jobs"][0])
        assert jf["status"] == "error" and "mesh import failed" in jf["error"], jf

        assert c.delete(f"/api/jobs/{jid}").status_code == 204
        assert c.get(f"/api/jobs/{jid}").status_code == 404
        assert len(c.get("/api/jobs").json()["jobs"]) == 5

        h = c.get("/").headers
        assert "script-src 'self'" in h["content-security-policy"]
    print("ok")


if __name__ == "__main__":
    run()
