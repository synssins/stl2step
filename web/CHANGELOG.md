# Changelog — stl2step-web

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
