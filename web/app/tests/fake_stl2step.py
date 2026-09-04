"""Stand-in for the stl2step CLI: same argv shape, same contract line, no OCCT."""
import json
import struct
import sys

argv = sys.argv[1:]


def arg(flag, default=None):
    return argv[argv.index(flag) + 1] if flag in argv else default


if "--mesh" in argv:
    for bad in ("--engine", "--schema", "--units"):
        if bad in argv:
            print("error: %s is not valid in mesh mode" % bad, file=sys.stderr)
            sys.exit(1)
    import os
    import shutil
    out = arg("-o")
    shutil.copyfile(os.path.join(os.path.dirname(arg("--mesh")), "input.stl"), out)
    with open(out, "rb") as f:
        raw = f.read()
    tri = struct.unpack_from("<I", raw, 80)[0] if len(raw) >= 84 else 0
    if 84 + 50 * tri != len(raw):
        tri = 0
    lo, hi = [1e30] * 3, [-1e30] * 3
    for i in range(tri):
        for v in range(3):
            x, y, z = struct.unpack_from("<3f", raw, 84 + 50 * i + 12 + 12 * v)
            lo = [min(lo[0], x), min(lo[1], y), min(lo[2], z)]
            hi = [max(hi[0], x), max(hi[1], y), max(hi[2], z)]
    edges = arg("--edges")
    nseg = 0
    if edges:
        c = [[lo[0], lo[1], lo[2]], [hi[0], lo[1], lo[2]], [hi[0], hi[1], lo[2]], [lo[0], hi[1], lo[2]],
             [lo[0], lo[1], hi[2]], [hi[0], lo[1], hi[2]], [hi[0], hi[1], hi[2]], [lo[0], hi[1], hi[2]]]
        pairs = [(0, 1), (1, 2), (2, 3), (3, 0), (4, 5), (5, 6), (6, 7), (7, 4), (0, 4), (1, 5), (2, 6), (3, 7)]
        with open(edges, "wb") as f:
            if tri:
                for a, b in pairs:
                    f.write(struct.pack("<6f", *c[a], *c[b]))
                nseg = len(pairs)
    r = {"ok": True, "faces": 35, "edges": nseg, "triangles": tri, "seconds": 0.01, "input": arg("--mesh"), "output": out}
    if edges:
        r["edgesFile"] = edges
    print("MESH_RESULT " + json.dumps(r))
    sys.exit(0)

inp, out = argv[0], arg("-o")
with open(out, "w") as f:
    f.write("ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\nENDSEC;\nEND-ISO-10303-21;\n")
r = {"ok": True, "exitCode": 0, "input": inp, "output": out, "triangles": 908, "vertices": 454,
     "components": 1, "solids": 1, "openShells": 0, "facesBeforeUnify": 908, "facesAfterUnify": 434,
     "meshVolumeMM3": 1.0, "stepVolumeMM3": 1.0, "volumeDeltaPct": 0.0, "watertight": True,
     "seconds": 0.01, "warnings": []}
if arg("--engine") == "trueform":
    r.update({"smoothPlanes": 23, "smoothCylinders": 15, "smoothFillets": 0, "smoothRejected": 0,
              "facesAfterSmooth": 35})
code = 0
with open(inp, "rb") as f:
    hdr = f.read(80).lower()
if b"warn" in hdr or b"open" in hdr:
    r["warnings"] = ["smooth: IntAna cyl|cyl empty/same — keeping mesh polyline"] * 4 + [
        "smooth: IntAna plane|plane empty/same — keeping mesh polyline",
        "J6: shell not closed freeEdges=16 faces=931 recover=0",
        "smooth: analytic rebuild reverted on one component -- kept faceted"]
    r["exitCode"] = code = 2
    if b"open" in hdr:
        r.update({"watertight": False, "openShells": 1})
print("RESULT " + json.dumps(r))
sys.exit(code)
