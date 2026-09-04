# Changelog — stl2step-web

Version shown in the UI header comes from `WEB_VERSION` in `app/main.py`; bump it with each release.

## 0.2.0 — 2026-09-04

- UI header shows `v<web> · engine <stl2step --version>`; `/api/health` gains `version` and `engine`.
- App files (`/`, `app.js`, `viewer.js`, `style.css`) are served `Cache-Control: no-cache` so a redeploy
  never needs a hard refresh; `/vendor/` stays cacheable.

## 2026-09-04 (input formats)

- Browser preview for OBJ, FBX, PLY and 3MF before conversion: three.js `OBJLoader`, `FBXLoader`, `PLYLoader`,
  `3MFLoader` (+ `fflate`, NURBS helpers) vendored from the same 0.185.1 tarball; every mesh in the file is
  merged into one soup in world space. Loaders never fetch textures (URL modifier → 1×1 data-URI gif).
  Server-side assimp import remains what the engine converts.
- Fix: delete confirmation lost its armed state when the history list re-rendered (thumbnail upload,
  polling), so the second click only re-armed. State now lives in app state; armed button reads "Delete?",
  4 s window.
- OBJ, FBX, PLY and 3MF accepted. Kind sniffed from content (binary STL layout, `solid…facet`,
  `Kaydara FBX Binary` / `; FBX`, `ply`, zip with `3D/*.model`, OBJ `v` lines); display name follows the
  sniffed kind. Non-STL uploads are stored as `source.<kind>` and converted to `input.stl` by `assimp export`
  in the worker before stl2step runs (`assimp-utils` added to both images; `ASSIMP_BIN` env).
  `GET input.stl` answers 409 while the import has not run yet. `/api/health` reports `importer` and `formats`.
- Warnings: repeat count shown as a `(4×)` prefix; wording now says the IntAna/J6 failures break the TrueForm
  rebuild that was discarded, not the faceted file that was written.
- `tests/fake_assimp.py` (OBJ → binary STL) so the import path is covered by `test_api.py`.

## 2026-09-04 (host fixes)

- History rows show engine + schema used (`TrueForm AP214`).
- Warnings box: identical warnings grouped with a count, each known engine warning followed by a plain-English
  explanation; title says "TrueForm reverted, result is faceted" when that happened.
- Viewer: third shading mode **Feature edges** (facet edges hidden below a 20° crease, the CAD look);
  open edges of the input mesh (edges owned by one triangle) drawn in red with a count in the status line.
- Fake converter emits the XendStop warning set / open-shell result when the STL header contains
  `warn` / `open`, for UI testing.
- `Dockerfile.occt79`: same image against conda-forge OpenCASCADE 7.9 (the engine's CI calibration target).
  Motivation: XendStop.stl in TrueForm on 7.6.3 hit `IntAna cyl|cyl empty/same` ×4 → J6 16 free edges →
  component reverted to faceted (see repo `FINDINGS-CYLEDGES.md`).
- `main.py`: CLI subprocess env now passes `LD_LIBRARY_PATH` and `STL2STEP_*` through (was `PATH` only).
- UI: with a history job selected and no new files picked, the primary button becomes
  "Convert again · Verbatim|TrueForm" and reconverts that job with the current panel options
  (same as the ↻ row icon). First run on the Docker host confirmed: `converter: true`, real conversions.
- `entrypoint.sh`: start as root, set `app` to `PUID`/`PGID`, chown `/data` when needed, drop privileges
  with `setpriv`. Compose snippet uses `PUID`/`PGID` env instead of `user:`; no host-side chown.
- Dockerfile: `app` user no longer pinned to UID 1000 (`ubuntu:24.04` already ships one).

## 2026-09-04

- Moved into the fork as `web/`. Dockerfile now builds from the repository root (no in-image `git clone`);
  `occt-link.patch` applied with `patch -p1` in the builder stage. `compose-snippet.yml` added for the home
  stack (builds from the GitHub URL, port 8480, `user: ${PUID}:${PGID}`).
- Fork CI: `web/` classified as docs-only in `scripts/ci-local-gate.sh`; `paths-ignore: web/**` in
  `.github/workflows/ci.yml`.
- API: `GET /api/jobs/{id}/input.stl`, `preview.stl`, `preview.edges` (mesh-mode pass runs lazily on first
  request, on its own worker, cached as `preview.json`), `GET/POST /api/jobs/{id}/thumb` (PNG, ≤ 512 KB,
  magic-checked), `POST /api/jobs/{id}/reconvert`. Job JSON gains `preview` and `thumb`. Gzip middleware.
- UI rewritten to the SolidOut spec: dark theme, viewport with Import/STEP and Shaded/Wireframe segments,
  triad, status line; left sidebar with Files (drop zone, local preview before converting) and History
  (thumbnail, status dot, faces, age, reconvert, two-click delete); right Convert panel with segmented
  engine/schema/units, tolerance stepper, Advanced section, result card (planes/cylinders/fillets/faces,
  volume delta, watertight pill, expandable warnings). Stacks on phones.
- three.js 0.185.1 vendored (`three.module.js`, `three.core.js`, `STLLoader.js`, `OrbitControls.js`).
- `test_api.py` + `tests/fake_stl2step.py`: end-to-end API test without OCCT.
- README.

## 2026-09-04 (v0, sandbox)

- Initial backend: FastAPI wrapper, SQLite job table, queue worker, sync `/api/convert`, security headers,
  upload validation. Placeholder light-theme UI.
