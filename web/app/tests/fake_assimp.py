"""Stand-in for `assimp export <in> <out> -fstlb ...`: reads OBJ (v/f lines), writes binary STL."""
import struct
import sys

if len(sys.argv) < 4 or sys.argv[1] != "export":
    print("usage: assimp export <in> <out> [-fstlb] [flags]", file=sys.stderr)
    sys.exit(1)
src, dst = sys.argv[2], sys.argv[3]
verts, tris = [], []
with open(src, "r", errors="replace") as f:
    for line in f:
        parts = line.split()
        if not parts:
            continue
        if parts[0] == "v":
            verts.append(tuple(float(x) for x in parts[1:4]))
        elif parts[0] == "f":
            idx = [int(p.split("/")[0]) for p in parts[1:]]
            idx = [i - 1 if i > 0 else len(verts) + i for i in idx]
            for k in range(1, len(idx) - 1):
                tris.append((idx[0], idx[k], idx[k + 1]))
if not tris:
    print("error: no faces found in " + src, file=sys.stderr)
    sys.exit(1)
with open(dst, "wb") as out:
    out.write(b"fake-assimp".ljust(80, b"\0") + struct.pack("<I", len(tris)))
    for a, b, c in tris:
        out.write(struct.pack("<3f", 0, 0, 0))
        for v in (a, b, c):
            out.write(struct.pack("<3f", *verts[v]))
        out.write(b"\0\0")
print("Exported %d triangles" % len(tris))
