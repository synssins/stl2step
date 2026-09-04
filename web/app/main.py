"""stl2step-web — thin FastAPI wrapper around the stl2step CLI.

Flow: upload STL -> job dir under DATA_DIR/jobs/<uuid> -> queued -> worker runs
`stl2step` as a subprocess -> parse the trailing `RESULT {json}` line -> STEP
available for download. Job metadata lives in SQLite (DATA_DIR/jobs.db).

Security posture: LAN-only, no auth. Input is still validated (extension,
structure, size), files are renamed to UUIDs, options are whitelisted enums and
bounded numbers, and the CLI is invoked with an argv list (no shell).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import sqlite3
import struct
import subprocess
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal, Optional

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError

log = logging.getLogger("stl2step-web")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# ---------- config ----------
WEB_VERSION = "0.2.0"
STL2STEP_BIN = os.environ.get("STL2STEP_BIN", "/usr/local/bin/stl2step")
ASSIMP_BIN = os.environ.get("ASSIMP_BIN", "/usr/bin/assimp")
ENGINE_VERSION = ""
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
JOBS_DIR = DATA_DIR / "jobs"
DB_PATH = DATA_DIR / "jobs.db"
MAX_UPLOAD_BYTES = int(float(os.environ.get("MAX_UPLOAD_MB", "200")) * 1024 * 1024)
JOB_TIMEOUT_SEC = int(os.environ.get("JOB_TIMEOUT_SEC", "600"))
CONCURRENCY = max(1, int(os.environ.get("CONCURRENCY", "1")))
RETAIN_DAYS = int(os.environ.get("RETAIN_DAYS", "30"))
STATIC_DIR = Path(__file__).parent / "static"
CLI_ENV = {"PATH": "/usr/local/bin:/usr/bin:/bin",
           **{k: v for k, v in os.environ.items() if k == "LD_LIBRARY_PATH" or k.startswith("STL2STEP_")}}
CLI_ENV.pop("STL2STEP_BIN", None)

THUMB_MAX_BYTES = 512 * 1024
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

JOB_ID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


# ---------- options (whitelisted; the only thing that reaches argv) ----------
class ConvertOptions(BaseModel):
    engine: Literal["verbatim", "trueform"] = "verbatim"
    units: Literal["mm", "in"] = "mm"
    schema_: Literal["AP203", "AP214", "AP242"] = Field("AP214", alias="schema")
    scale: float = Field(1.0, gt=0.0, le=10000.0)
    weld: float = Field(0.0, ge=0.0, le=100.0)
    verify: bool = True
    unify: bool = True
    smooth_fillets: bool = True
    smooth_tol: float = Field(0.0, ge=0.0, le=100.0)
    smooth_angle: float = Field(2.0, gt=0.0, le=45.0)
    threads: int = Field(0, ge=0, le=128)

    model_config = {"populate_by_name": True}

    def argv(self, inp: Path, out: Path) -> list[str]:
        a = [STL2STEP_BIN, str(inp), "-o", str(out), "--quiet",
             "--schema", self.schema_, "--units", self.units,
             "--engine", self.engine, "--threads", str(self.threads)]
        if self.scale != 1.0:
            a += ["--scale", repr(self.scale)]
        if self.weld > 0:
            a += ["--weld", repr(self.weld)]
        if not self.verify:
            a.append("--no-verify")
        if not self.unify:
            a.append("--no-unify")
        if self.engine == "trueform":
            if not self.smooth_fillets:
                a.append("--no-smooth-fillets")
            if self.smooth_tol > 0:
                a += ["--smooth-tol", repr(self.smooth_tol)]
            if self.smooth_angle != 2.0:
                a += ["--smooth-angle", repr(self.smooth_angle)]
        return a


def parse_options(form: dict) -> ConvertOptions:
    """Build options from multipart form fields. Unknown fields are ignored (no mass assignment)."""
    allowed = {"engine", "units", "schema", "scale", "weld", "verify", "unify",
               "smooth_fillets", "smooth_tol", "smooth_angle", "threads"}
    raw = {k: v for k, v in form.items() if k in allowed and isinstance(v, str)}
    for b in ("verify", "unify", "smooth_fillets"):
        if b in raw:
            raw[b] = raw[b].lower() in ("1", "true", "on", "yes")
    try:
        return ConvertOptions(**raw)
    except ValidationError as e:
        raise HTTPException(400, detail="Invalid options: " + "; ".join(
            f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in e.errors()))


# ---------- db ----------
def db() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def db_init() -> None:
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    with db() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            created REAL NOT NULL,
            started REAL,
            finished REAL,
            source TEXT NOT NULL,
            name TEXT NOT NULL,
            input_bytes INTEGER NOT NULL,
            options TEXT NOT NULL,
            status TEXT NOT NULL,
            exit_code INTEGER,
            result TEXT,
            error TEXT,
            step_bytes INTEGER
        )""")
        c.execute("CREATE INDEX IF NOT EXISTS jobs_created ON jobs(created DESC)")
        # Anything left 'running' when we start was killed mid-flight.
        c.execute("UPDATE jobs SET status='error', error='interrupted by restart', finished=? WHERE status='running'",
                  (time.time(),))


def row_to_dict(r: sqlite3.Row) -> dict:
    d = dict(r)
    d["options"] = json.loads(d["options"]) if d["options"] else {}
    d["result"] = json.loads(d["result"]) if d["result"] else None
    job_dir = JOBS_DIR / d["id"]
    pj = job_dir / "preview.json"
    try:
        d["preview"] = json.loads(pj.read_text()) if pj.exists() else None
    except (OSError, json.JSONDecodeError):
        d["preview"] = None
    d["thumb"] = (job_dir / "thumb.png").exists()
    return d


# ---------- upload validation ----------
MESH_KINDS = ("stl", "obj", "fbx", "ply", "3mf")


def sniff_mesh(path: Path) -> str:
    """Return the mesh kind from content, ignoring the extension. Raises 400 for anything else."""
    size = path.stat().st_size
    if size < 15:
        raise HTTPException(400, "File is too small to be a mesh")
    with path.open("rb") as f:
        head = f.read(65536)
    if size >= 84:
        (n,) = struct.unpack_from("<I", head, 80)
        if 84 + 50 * n == size:
            return "stl"
    low = head.lower()
    if low.lstrip()[:5] == b"solid" and b"facet" in low:
        return "stl"
    if head.startswith(b"Kaydara FBX Binary") or low.lstrip().startswith(b"; fbx"):
        return "fbx"
    if head.startswith(b"ply\n") or head.startswith(b"ply\r\n"):
        return "ply"
    if head.startswith(b"PK\x03\x04"):
        try:
            import zipfile
            with zipfile.ZipFile(path) as z:
                if any(n.lower().startswith("3d/") and n.lower().endswith(".model") for n in z.namelist()):
                    return "3mf"
        except zipfile.BadZipFile:
            pass
        raise HTTPException(400, "Zip file is not a 3MF")
    if re.search(rb"(?m)^\s*v\s+-?[\d.]", head):
        return "obj"
    raise HTTPException(400, "Not a recognisable mesh (STL, OBJ, FBX, PLY or 3MF)")


async def save_upload(up: UploadFile, dest: Path) -> int:
    written = 0
    with dest.open("wb") as f:
        while True:
            chunk = await up.read(1024 * 1024)
            if not chunk:
                break
            written += len(chunk)
            if written > MAX_UPLOAD_BYTES:
                f.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(413, f"File exceeds {MAX_UPLOAD_BYTES // (1024*1024)} MB limit")
            f.write(chunk)
    return written


def display_name(filename: Optional[str], kind: str) -> str:
    """Safe display name; the extension always reflects the sniffed kind, whatever the upload was called."""
    base = Path(filename or f"part.{kind}").name
    base = SAFE_NAME_RE.sub("_", base).strip("._") or f"part.{kind}"
    stem, ext = os.path.splitext(base)
    base = f"{stem}.{kind}" if ext.lower().lstrip(".") in MESH_KINDS else f"{base}.{kind}"
    return base[:120]


# ---------- conversion (runs in thread pool) ----------
def import_mesh(job_dir: Path) -> Optional[str]:
    """Non-STL upload -> input.stl via assimp. Returns an error string, or None when input.stl exists."""
    inp = job_dir / "input.stl"
    if inp.exists():
        return None
    src = next((p for p in job_dir.glob("source.*")), None)
    if src is None:
        return "input mesh missing"
    argv = [ASSIMP_BIN, "export", str(src), str(inp), "-fstlb", "-ptv", "-tri", "-jiv"]
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=JOB_TIMEOUT_SEC,
                           cwd=job_dir, env=CLI_ENV)
    except subprocess.TimeoutExpired:
        return f"mesh import timed out after {JOB_TIMEOUT_SEC}s"
    except OSError as e:
        return f"could not start mesh importer ({e.__class__.__name__})"
    (job_dir / "import.txt").write_text(p.stdout + p.stderr)
    if p.returncode != 0 or not inp.exists() or inp.stat().st_size < 84:
        inp.unlink(missing_ok=True)
        tail = (p.stderr.strip() or p.stdout.strip()).splitlines()
        return "mesh import failed: " + (tail[-1][:300] if tail else f"assimp exit {p.returncode}")
    return None


def run_conversion(job_dir: Path, opts: ConvertOptions) -> dict:
    inp, out = job_dir / "input.stl", job_dir / "output.step"
    t0 = time.time()
    err = import_mesh(job_dir)
    if err:
        return {"exit_code": None, "result": None, "seconds": time.time() - t0, "error": err}
    argv = opts.argv(inp, out)
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=JOB_TIMEOUT_SEC,
                           cwd=job_dir, env=CLI_ENV)
        (job_dir / "stdout.txt").write_text(p.stdout)
        (job_dir / "stderr.txt").write_text(p.stderr)
        result = None
        for line in reversed(p.stdout.splitlines()):
            if line.startswith("RESULT "):
                try:
                    result = json.loads(line[7:])
                except json.JSONDecodeError:
                    result = None
                break
        return {"exit_code": p.returncode, "result": result, "seconds": time.time() - t0,
                "error": None if result else (p.stderr.strip().splitlines() or ["no RESULT line"])[-1][:500]}
    except subprocess.TimeoutExpired:
        return {"exit_code": None, "result": None, "seconds": time.time() - t0,
                "error": f"timed out after {JOB_TIMEOUT_SEC}s"}
    except OSError as e:
        log.exception("spawn failed")
        return {"exit_code": None, "result": None, "seconds": time.time() - t0,
                "error": f"could not start converter ({e.__class__.__name__})"}


def run_mesh_pass(job_dir: Path) -> dict:
    """Mesh mode: tessellate output.step -> preview.stl + preview.edges (Format A). Exit 0 ok, 1 failed."""
    argv = [STL2STEP_BIN, "--mesh", str(job_dir / "output.step"), "-o", str(job_dir / "preview.stl"),
            "--edges", str(job_dir / "preview.edges"), "--quiet"]
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=JOB_TIMEOUT_SEC,
                           cwd=job_dir, env=CLI_ENV)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"mesh pass timed out after {JOB_TIMEOUT_SEC}s"}
    except OSError as e:
        return {"ok": False, "error": f"could not start converter ({e.__class__.__name__})"}
    for line in reversed(p.stdout.splitlines()):
        if line.startswith("MESH_RESULT "):
            try:
                return json.loads(line[12:])
            except json.JSONDecodeError:
                break
    return {"ok": False, "error": (p.stderr.strip().splitlines() or ["no MESH_RESULT line"])[-1][:500]}


def status_from(res: dict) -> str:
    if res["result"] and res["result"].get("ok"):
        return "warn" if res["exit_code"] == 2 or res["result"].get("warnings") else "done"
    if res["result"] is not None:
        return "failed"       # converter ran, said no
    return "error"            # converter didn't run cleanly


def finish_job(job_id: str, res: dict) -> None:
    step = JOBS_DIR / job_id / "output.step"
    step_bytes = step.stat().st_size if step.exists() else None
    with db() as c:
        c.execute("""UPDATE jobs SET status=?, exit_code=?, result=?, error=?, step_bytes=?, finished=?
                     WHERE id=?""",
                  (status_from(res), res["exit_code"],
                   json.dumps(res["result"]) if res["result"] else None,
                   res["error"] or (res["result"] or {}).get("error"),
                   step_bytes, time.time(), job_id))


# ---------- queue / worker ----------
queue: asyncio.Queue[str] = asyncio.Queue()
pool = ThreadPoolExecutor(max_workers=CONCURRENCY)
# ponytail: previews get their own single worker so history browsing is not stuck behind a long conversion;
# one global lock serialises them, per-job locks if two viewers ever contend.
preview_pool = ThreadPoolExecutor(max_workers=1)
preview_lock = asyncio.Lock()


async def ensure_preview(job: dict) -> dict:
    job_dir = JOBS_DIR / job["id"]
    if job["status"] not in ("done", "warn") or not (job_dir / "output.step").exists():
        raise HTTPException(404, "No STEP output for this job")
    async with preview_lock:
        pj = job_dir / "preview.json"
        if pj.exists() and (job_dir / "preview.stl").exists():
            return json.loads(pj.read_text())
        res = await asyncio.get_running_loop().run_in_executor(preview_pool, run_mesh_pass, job_dir)
        if not res.get("ok"):
            raise HTTPException(502, "Preview failed: " + str(res.get("error", "unknown"))[:200])
        if not (job_dir / "preview.edges").exists():
            (job_dir / "preview.edges").write_bytes(b"")
        pj.write_text(json.dumps({k: res[k] for k in ("faces", "edges", "triangles", "seconds") if k in res}))
        return json.loads(pj.read_text())


async def worker() -> None:
    loop = asyncio.get_running_loop()
    while True:
        job_id = await queue.get()
        try:
            with db() as c:
                row = c.execute("SELECT options, status FROM jobs WHERE id=?", (job_id,)).fetchone()
                if not row or row["status"] != "queued":
                    continue
                c.execute("UPDATE jobs SET status='running', started=? WHERE id=?", (time.time(), job_id))
            opts = ConvertOptions(**json.loads(row["options"]))
            res = await loop.run_in_executor(pool, run_conversion, JOBS_DIR / job_id, opts)
            finish_job(job_id, res)
        except Exception:
            log.exception("worker failed on %s", job_id)
            finish_job(job_id, {"exit_code": None, "result": None, "seconds": 0, "error": "internal error"})
        finally:
            queue.task_done()


async def retention_loop() -> None:
    while True:
        if RETAIN_DAYS > 0:
            cutoff = time.time() - RETAIN_DAYS * 86400
            try:
                with db() as c:
                    old = [r["id"] for r in c.execute(
                        "SELECT id FROM jobs WHERE created<? AND status NOT IN ('queued','running')", (cutoff,))]
                    for jid in old:
                        shutil.rmtree(JOBS_DIR / jid, ignore_errors=True)
                    if old:
                        c.executemany("DELETE FROM jobs WHERE id=?", [(j,) for j in old])
                        log.info("retention: removed %d jobs", len(old))
            except Exception:
                log.exception("retention pass failed")
        await asyncio.sleep(3600)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global ENGINE_VERSION
    db_init()
    try:
        v = subprocess.run([STL2STEP_BIN, "--version"], capture_output=True, text=True, timeout=10, env=CLI_ENV)
        ENGINE_VERSION = (v.stdout.strip() or v.stderr.strip()).splitlines()[0][:40] if (v.stdout or v.stderr) else ""
    except (OSError, subprocess.TimeoutExpired):
        ENGINE_VERSION = ""
    if not Path(STL2STEP_BIN).exists():
        log.error("stl2step binary not found at %s", STL2STEP_BIN)
    with db() as c:
        for r in c.execute("SELECT id FROM jobs WHERE status='queued' ORDER BY created"):
            queue.put_nowait(r["id"])
    tasks = [asyncio.create_task(worker()) for _ in range(CONCURRENCY)]
    tasks.append(asyncio.create_task(retention_loop()))
    yield
    for t in tasks:
        t.cancel()


app = FastAPI(title="stl2step-web", docs_url="/api/docs", redoc_url=None, openapi_url="/api/openapi.json",
              lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=1024)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers["Content-Security-Policy"] = ("default-src 'self'; script-src 'self'; style-src 'self'; "
                                               "img-src 'self' data:; frame-ancestors 'none'; base-uri 'self'; "
                                               "form-action 'self'")
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    p = request.url.path
    if not p.startswith("/api/") and not p.startswith("/vendor/") and "Cache-Control" not in resp.headers:
        resp.headers["Cache-Control"] = "no-cache"
    return resp


def load_job(job_id: str) -> dict:
    if not JOB_ID_RE.match(job_id):
        raise HTTPException(404, "Job not found")
    with db() as c:
        r = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not r:
        raise HTTPException(404, "Job not found")
    return row_to_dict(r)


async def ingest(up: UploadFile, opts: ConvertOptions, source: str) -> str:
    job_id = str(uuid.uuid4())
    job_dir = JOBS_DIR / job_id
    job_dir.mkdir(parents=True)
    try:
        tmp = job_dir / "upload.tmp"
        size = await save_upload(up, tmp)
        kind = sniff_mesh(tmp)
        tmp.rename(job_dir / ("input.stl" if kind == "stl" else f"source.{kind}"))
        name = display_name(up.filename, kind)
    except HTTPException:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise
    with db() as c:
        c.execute("INSERT INTO jobs (id, created, source, name, input_bytes, options, status) VALUES (?,?,?,?,?,?,?)",
                  (job_id, time.time(), source, name, size, opts.model_dump_json(by_alias=True), "queued"))
    return job_id


# ---------- API ----------
@app.get("/api/health")
def health():
    return {"ok": True, "version": WEB_VERSION, "engine": ENGINE_VERSION,
            "converter": Path(STL2STEP_BIN).exists(), "importer": Path(ASSIMP_BIN).exists(),
            "formats": list(MESH_KINDS), "queued": queue.qsize()}


@app.get("/api/defaults")
def defaults():
    return {"options": ConvertOptions().model_dump(by_alias=True),
            "max_upload_mb": MAX_UPLOAD_BYTES // (1024 * 1024), "retain_days": RETAIN_DAYS}


@app.post("/api/jobs", status_code=202)
async def submit_jobs(request: Request, files: list[UploadFile] = File(...)):
    form = await request.form()
    opts = parse_options(dict(form))
    if not files or len(files) > 50:
        raise HTTPException(400, "Provide 1–50 STL files")
    ids = []
    for up in files:
        ids.append(await ingest(up, opts, "web"))
    for jid in ids:
        queue.put_nowait(jid)
    return {"jobs": ids}


@app.get("/api/jobs")
def list_jobs(limit: int = 100):
    limit = max(1, min(limit, 500))
    with db() as c:
        rows = c.execute("SELECT * FROM jobs ORDER BY created DESC LIMIT ?", (limit,)).fetchall()
    return {"jobs": [row_to_dict(r) for r in rows]}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    return load_job(job_id)


@app.get("/api/jobs/{job_id}/step")
def download_step(job_id: str):
    job = load_job(job_id)
    path = JOBS_DIR / job_id / "output.step"
    if job["status"] not in ("done", "warn") or not path.exists():
        raise HTTPException(404, "No STEP output for this job")
    fname = Path(job["name"]).stem + ".step"
    return FileResponse(path, media_type="application/step", filename=fname,
                        headers={"Cache-Control": "no-store"})


@app.get("/api/jobs/{job_id}/input.stl")
def download_input(job_id: str):
    job = load_job(job_id)
    path = JOBS_DIR / job_id / "input.stl"
    if not path.exists():
        if job["status"] in ("queued", "running"):
            raise HTTPException(409, "Mesh not imported yet")
        raise HTTPException(404, "Input no longer available")
    return FileResponse(path, media_type="model/stl", filename=job["name"],
                        content_disposition_type="inline", headers={"Cache-Control": "private, max-age=86400"})


@app.get("/api/jobs/{job_id}/preview.stl")
async def preview_stl(job_id: str):
    await ensure_preview(load_job(job_id))
    return FileResponse(JOBS_DIR / job_id / "preview.stl", media_type="model/stl",
                        headers={"Cache-Control": "no-store"})


@app.get("/api/jobs/{job_id}/preview.edges")
async def preview_edges(job_id: str):
    await ensure_preview(load_job(job_id))
    return FileResponse(JOBS_DIR / job_id / "preview.edges", media_type="application/octet-stream",
                        headers={"Cache-Control": "no-store"})


@app.get("/api/jobs/{job_id}/thumb")
def get_thumb(job_id: str):
    load_job(job_id)
    path = JOBS_DIR / job_id / "thumb.png"
    if not path.exists():
        raise HTTPException(404, "No thumbnail")
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "private, max-age=86400"})


@app.post("/api/jobs/{job_id}/thumb", status_code=204)
async def put_thumb(job_id: str, request: Request):
    job = load_job(job_id)
    if job["status"] not in ("done", "warn"):
        raise HTTPException(409, "Job has no result to thumbnail")
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > THUMB_MAX_BYTES:
            raise HTTPException(413, f"Thumbnail exceeds {THUMB_MAX_BYTES // 1024} KB")
    if not body.startswith(PNG_MAGIC):
        raise HTTPException(400, "Thumbnail must be a PNG")
    (JOBS_DIR / job_id / "thumb.png").write_bytes(body)
    return Response(status_code=204)


@app.post("/api/jobs/{job_id}/reconvert", status_code=202)
async def reconvert(job_id: str, request: Request):
    src = load_job(job_id)
    src_stl = JOBS_DIR / job_id / "input.stl"
    if not src_stl.exists():
        raise HTTPException(404, "Input no longer available")
    opts = parse_options(dict(await request.form()))
    new_id = str(uuid.uuid4())
    new_dir = JOBS_DIR / new_id
    new_dir.mkdir(parents=True)
    shutil.copyfile(src_stl, new_dir / "input.stl")
    with db() as c:
        c.execute("INSERT INTO jobs (id, created, source, name, input_bytes, options, status) VALUES (?,?,?,?,?,?,?)",
                  (new_id, time.time(), "web", src["name"], src["input_bytes"],
                   opts.model_dump_json(by_alias=True), "queued"))
    queue.put_nowait(new_id)
    return {"jobs": [new_id]}


@app.get("/api/jobs/{job_id}/log")
def job_log(job_id: str):
    load_job(job_id)
    d = JOBS_DIR / job_id
    out = (d / "stdout.txt").read_text(errors="replace") if (d / "stdout.txt").exists() else ""
    err = (d / "stderr.txt").read_text(errors="replace") if (d / "stderr.txt").exists() else ""
    return {"stdout": out[-20000:], "stderr": err[-20000:]}


@app.delete("/api/jobs/{job_id}", status_code=204)
def delete_job(job_id: str):
    job = load_job(job_id)
    if job["status"] == "running":
        raise HTTPException(409, "Job is running")
    shutil.rmtree(JOBS_DIR / job_id, ignore_errors=True)
    with db() as c:
        c.execute("DELETE FROM jobs WHERE id=?", (job_id,))
    return Response(status_code=204)


@app.post("/api/convert")
async def convert_sync(request: Request, file: UploadFile = File(...)):
    """Synchronous one-shot for scripts / the Fusion add-in: STL in, STEP bytes out.
    RESULT JSON is echoed in the X-Stl2step-Result header; job is still recorded."""
    form = await request.form()
    opts = parse_options(dict(form))
    job_id = await ingest(file, opts, "api")
    with db() as c:
        c.execute("UPDATE jobs SET status='running', started=? WHERE id=?", (time.time(), job_id))
    loop = asyncio.get_running_loop()
    res = await loop.run_in_executor(pool, run_conversion, JOBS_DIR / job_id, opts)
    finish_job(job_id, res)
    job = load_job(job_id)
    if job["status"] not in ("done", "warn"):
        return JSONResponse(status_code=422, content={"job": job_id, "status": job["status"],
                                                      "error": job["error"], "result": job["result"]})
    path = JOBS_DIR / job_id / "output.step"
    return FileResponse(path, media_type="application/step", filename=Path(job["name"]).stem + ".step",
                        headers={"X-Stl2step-Result": json.dumps(job["result"], separators=(",", ":")),
                                 "X-Stl2step-Job": job_id, "Cache-Control": "no-store"})


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
