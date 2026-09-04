# stl2step-web

Web UI + HTTP API around the `stl2step` CLI (STL mesh → STEP B-Rep, OpenCASCADE).
One container: FastAPI + vendored three.js viewer + the CLI built from this repo.
LAN use, no auth.

- Convert STL files from a browser, preview the input mesh and the resulting solid (shaded / B-Rep edges).
- Job history: re-download, inspect result, reconvert with new options, delete.
- Synchronous `/api/convert` endpoint for scripts and the Fusion 360 add-in.

## Build and run

Build context is the **repository root** (the Dockerfile copies `CMakeLists.txt`, `cmake/`, `include/`, `src/`, `web/`).

```sh
# from the repo root
docker build -f web/Dockerfile -t stl2step-web .

# or with the standalone compose file
cd web && docker compose up -d --build
```

For an existing compose stack, paste the service from [`compose-snippet.yml`](compose-snippet.yml). It builds
straight from GitHub (`context: https://github.com/synssins/stl2step.git#main`, `dockerfile: web/Dockerfile`)
and publishes port **8480**. Rebuild after a push with
`docker compose build --no-cache stl2step-web && docker compose up -d stl2step-web`.

The container starts as root, sets the `app` user to `PUID`/`PGID` (default 1000/1000), chowns `/data` if
needed, then drops privileges with `setpriv` before starting uvicorn. No host-side `chown` is required.

The builder stage (`apt` OCCT dev packages + compile, ~1 min on 4 cores) is a cached Docker layer; only source changes rebuild it.

### Environment

| Variable | Default | Meaning |
|---|---|---|
| `PUID`, `PGID` | `1000` | UID/GID the app runs as; `/data` is chowned to match at start. |
| `MAX_UPLOAD_MB` | `200` | Per-file upload cap. |
| `JOB_TIMEOUT_SEC` | `600` | Kill a conversion (or preview tessellation) after this. |
| `CONCURRENCY` | `1` | Parallel conversions. Keep at 1: stl2step already uses every core per job. |
| `RETAIN_DAYS` | `30` | Delete finished jobs older than this (`0` = keep forever). |
| `DATA_DIR` | `/data` | Job files + `jobs.db` (SQLite). Mount it. |
| `STL2STEP_BIN` | `/usr/local/bin/stl2step` | CLI path. |
| `ASSIMP_BIN` | `/usr/bin/assimp` | Mesh importer for non-STL uploads. |

### The CMake patch (`occt-link.patch`)

Upstream `CMakeLists.txt` does not link `TKPrim` and `TKBO`, which the TrueForm prism-rebuild path uses
(`BRepPrimAPI_MakePrism`, `BRepAlgoAPI_Fuse`). Homebrew's OCCT pulls them in transitively; Ubuntu's does not,
so the link fails. The patch adds both toolkits with the same `find_library` fallback the repo already uses for
`TKMesh`. It is applied in the builder stage with `patch -p1`; once the change lands in `CMakeLists.txt`
proper (it needs the three-platform CI gate), drop the `COPY`/`patch` lines from the Dockerfile.

### OCCT version

The image uses Ubuntu 24.04's OpenCASCADE **7.6.3**. The engine's CI is calibrated against 7.9, and TrueForm's
recogniser can find fewer cylinders on 7.6 for some meshes. Verified on 7.6.3: `tests/corpus/handle-lock.stl`
(908 triangles) → Verbatim 434 faces, TrueForm 35 faces (23 planes, 15 cylinders built, 0 rejected,
volume delta 0.000 %). [`Dockerfile.occt79`](Dockerfile.occt79) is the same image built against conda-forge
OCCT 7.9 (micromamba, `CMAKE_PREFIX_PATH=/opt/occt`, `LD_LIBRARY_PATH=/opt/occt/lib` at runtime); select it
with `dockerfile: web/Dockerfile.occt79` in compose. `LD_LIBRARY_PATH` and any `STL2STEP_*` environment
variables (engine diagnostics) are passed through to the CLI subprocess.

## Input formats

STL (binary or ASCII), OBJ, FBX, PLY, 3MF. The kind is detected from the file content, not the extension;
a mislabelled upload gets its display name corrected. Anything that is not STL is converted to binary STL
first by [assimp](https://github.com/assimp/assimp) (`assimp export in out -fstlb -ptv -tri -jiv`: node
transforms baked, polygons triangulated, duplicate vertices joined; materials and hierarchy dropped) and then
follows the normal path. The original upload is kept as `source.<kind>` in the job directory; `input.stl` is
what the viewer and the engine use.

Units are still yours to set: OBJ carries none, FBX usually means centimetres and assimp does not apply the
file's unit scale on export. Use `units` / `scale`.

## API

All responses are JSON unless noted. Job ids are UUIDv4. Options are multipart form fields; unknown fields are ignored.

| Field | Values | Default |
|---|---|---|
| `engine` | `verbatim` \| `trueform` | `verbatim` |
| `units` | `mm` \| `in` | `mm` |
| `schema` | `AP203` \| `AP214` \| `AP242` | `AP214` |
| `scale` | 0 < x ≤ 10000 | `1` |
| `weld` | 0 ≤ x ≤ 100 (mm) | `0` |
| `verify`, `unify`, `smooth_fillets` | `true` \| `false` | `true` |
| `smooth_tol` | 0 ≤ x ≤ 100 (mm, 0 = auto) | `0` |
| `smooth_angle` | 0 < x ≤ 45 (deg) | `2` |
| `threads` | 0–128 (0 = all cores) | `0` |

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | `{ok, converter, queued}` |
| GET | `/api/defaults` | default options, `max_upload_mb`, `retain_days` |
| POST | `/api/jobs` | multipart `files` (1–50) + options → `202 {jobs:[ids]}` |
| GET | `/api/jobs?limit=` | history, newest first |
| GET | `/api/jobs/{id}` | one job (`status`, `options`, `result`, `preview`, `thumb`, …) |
| GET | `/api/jobs/{id}/step` | download STEP (`Content-Disposition: attachment`) |
| GET | `/api/jobs/{id}/input.stl` | the uploaded mesh |
| GET | `/api/jobs/{id}/preview.stl` | tessellated STEP as binary STL (generated on first request, cached) |
| GET | `/api/jobs/{id}/preview.edges` | drawable B-Rep edges, Format A (raw LE float32 `x0 y0 z0 x1 y1 z1`, 24 B per segment) |
| GET / POST | `/api/jobs/{id}/thumb` | PNG thumbnail (POST body = PNG ≤ 512 KB; the browser renders and uploads it) |
| POST | `/api/jobs/{id}/reconvert` | options → new job from the stored input, `202 {jobs:[id]}` |
| GET | `/api/jobs/{id}/log` | stdout/stderr tails |
| DELETE | `/api/jobs/{id}` | remove job + files (`409` while running) |
| POST | `/api/convert` | **synchronous**: multipart `file` + options → STEP bytes |

Job `status`: `queued`, `running`, `done` (exit 0), `warn` (exit 2 or warnings), `failed` (converter said no),
`error` (did not run cleanly, timed out, or was interrupted by a restart).

`/api/convert` returns the STEP file with `X-Stl2step-Result` (the CLI's `RESULT` JSON) and `X-Stl2step-Job`
headers; on failure `422` with a JSON body. It never runs the preview pass — that happens lazily if someone
opens the job in the UI.

```sh
curl -F file=@part.stl -F engine=trueform http://host:8480/api/convert -o part.step -D headers.txt

curl -F files=@a.stl -F files=@b.stl -F engine=verbatim -F units=in http://host:8480/api/jobs
curl http://host:8480/api/jobs/<id>
curl -o a.step http://host:8480/api/jobs/<id>/step
curl -X DELETE http://host:8480/api/jobs/<id>
```

## Viewer

- three.js **0.185.1**, vendored in `app/static/vendor/` (see `VERSION.txt`). The bare `'three'` import in
  `STLLoader.js` / `OrbitControls.js` is rewritten to `./three.module.js` because the CSP (`script-src 'self'`)
  forbids an inline import map and CDN scripts.
- **Import** view shows the uploaded mesh; wireframe = facet edges (`EdgesGeometry`, 1° threshold).
- **STEP** view shows `preview.stl`; wireframe = the B-Rep edges from `preview.edges` drawn as `LineSegments`.
  That is what makes a TrueForm result visibly different from Verbatim.
- Z-up, orbit / pan / zoom, camera fitted to the bounding sphere on load, orientation triad bottom-left.
- Thumbnails are rendered client-side from the STEP view and posted to the server the first time a job is
  opened. API-only jobs show a placeholder until someone opens them.
- Options persist in `localStorage`. Keyboard reachable, visible focus rings, `prefers-reduced-motion` honoured.

## Security posture

LAN-only, no auth, but: extension + structure + size checks on uploads, UUID filenames, whitelisted option
enums and bounded numbers (the only thing that reaches `argv`), no shell, CSP / nosniff / frame-ancestors /
no-referrer headers, no stack traces to the client. Thumbnail uploads are magic-checked and size-capped.

## Development

```sh
cd web/app
python test_api.py        # end-to-end API test with tests/fake_stl2step.py standing in for the CLI
```

`tests/fake_stl2step.py` mimics the CLI contract (`RESULT` / `MESH_RESULT` lines, exit codes) without OCCT,
so the API and UI can be exercised on any machine. Point `STL2STEP_BIN` at a shim that runs it and start
`uvicorn main:app` to get a working UI with canned results.
