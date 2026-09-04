import * as THREE from './vendor/three.module.js';
import { STLLoader } from './vendor/STLLoader.js';
import { OBJLoader } from './vendor/OBJLoader.js';
import { PLYLoader } from './vendor/PLYLoader.js';
import { FBXLoader } from './vendor/FBXLoader.js';
import { ThreeMFLoader } from './vendor/3MFLoader.js';
import { OrbitControls } from './vendor/OrbitControls.js';

// Loaders never fetch textures: every URL resolves to a 1x1 data-URI gif (CSP img-src allows data:).
const quiet = new THREE.LoadingManager();
quiet.setURLModifier(() => 'data:image/gif;base64,R0lGODlhAQABAAAAACw=');
const stlLoader = new STLLoader();

export function parseSTL(buffer) {
  const g = stlLoader.parse(buffer);
  g.computeBoundingBox();
  g.computeBoundingSphere();
  return g;
}

// Any supported mesh file -> one non-indexed position-only BufferGeometry in world space.
// Browser-side preview only; the server converts the original with assimp for the engine.
export function parseMesh(name, buffer) {
  const ext = name.split('.').pop().toLowerCase();
  let root;
  if (ext === 'stl') return parseSTL(buffer);
  if (ext === 'obj') root = new OBJLoader(quiet).parse(new TextDecoder().decode(buffer));
  else if (ext === 'ply') root = new PLYLoader(quiet).parse(buffer);
  else if (ext === 'fbx') root = new FBXLoader(quiet).parse(buffer, '');
  else if (ext === '3mf') root = new ThreeMFLoader(quiet).parse(buffer);
  else throw new Error(`unsupported format .${ext}`);
  return toSoup(root);
}

function toSoup(root) {
  const chunks = [];
  let total = 0;
  const add = (geom, matrix) => {
    let g = geom.index ? geom.toNonIndexed() : geom.clone();
    if (matrix) g.applyMatrix4(matrix);
    const p = g.attributes.position;
    if (!p || p.count < 3) return;
    const arr = p.array.length === p.count * 3 ? p.array : new Float32Array(p.array.buffer, p.array.byteOffset, p.count * 3);
    chunks.push(arr);
    total += arr.length;
  };
  if (root.isBufferGeometry) add(root, null);
  else {
    root.updateMatrixWorld(true);
    root.traverse((o) => { if (o.isMesh && o.geometry) add(o.geometry, o.matrixWorld); });
  }
  if (!total) throw new Error('no triangles found');
  const merged = new Float32Array(total);
  let off = 0;
  for (const c of chunks) { merged.set(c, off); off += c.length; }
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.BufferAttribute(merged, 3));
  g.computeVertexNormals();
  g.computeBoundingBox();
  g.computeBoundingSphere();
  return g;
}

export function parseEdges(buffer) {
  const n = Math.floor(buffer.byteLength / 24) * 6;
  return n ? new Float32Array(buffer, 0, n) : null;
}

export function createViewer(canvas) {
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.setClearColor(0x141414, 1);
  renderer.autoClear = false;

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(40, 1, 0.1, 10000);
  camera.up.set(0, 0, 1);
  camera.position.set(120, -160, 100);

  const hemi = new THREE.HemisphereLight(0xffffff, 0x3a3a3a, 0.9);
  scene.add(hemi);
  const key = new THREE.DirectionalLight(0xffffff, 1.1);
  camera.add(key);
  key.position.set(0.6, 0.8, 1.2);
  scene.add(camera);

  const meshMat = new THREE.MeshStandardMaterial({
    color: 0xb9b9b9, roughness: 0.62, metalness: 0.08, flatShading: true, side: THREE.DoubleSide,
    polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1,
  });
  const edgeMat = new THREE.LineBasicMaterial({ color: 0xf2f2f2 });
  const openMat = new THREE.LineBasicMaterial({ color: 0xff4d4d, depthTest: false });

  const controls = new OrbitControls(camera, canvas);
  controls.enableDamping = false;
  controls.addEventListener('change', invalidate);

  const model = new THREE.Group();
  scene.add(model);
  let mesh = null, lines = null, featureLines = null, openLines = null, mode = 'shaded';

  // triad inset
  const triadScene = new THREE.Scene();
  const triadCam = new THREE.PerspectiveCamera(40, 1, 0.1, 100);
  triadCam.up.set(0, 0, 1);
  triadScene.add(makeTriad());

  let dirty = true, raf = 0;
  function invalidate() {
    dirty = true;
    if (!raf) raf = requestAnimationFrame(frame);
  }
  function frame() {
    raf = 0;
    if (!dirty) return;
    dirty = false;
    render();
  }
  function render() {
    const w = canvas.clientWidth, h = canvas.clientHeight;
    renderer.setViewport(0, 0, w, h);
    renderer.setScissor(0, 0, w, h);
    renderer.setScissorTest(true);
    renderer.clear();
    renderer.render(scene, camera);
    const s = Math.min(96, Math.floor(Math.min(w, h) * 0.22));
    renderer.setViewport(10, 10, s, s);
    renderer.setScissor(10, 10, s, s);
    triadCam.position.copy(camera.position).sub(controls.target).normalize().multiplyScalar(6);
    triadCam.lookAt(0, 0, 0);
    renderer.clearDepth();
    renderer.render(triadScene, triadCam);
    renderer.setScissorTest(false);
  }

  function resize() {
    const w = canvas.clientWidth, h = canvas.clientHeight;
    if (!w || !h) return;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    invalidate();
  }
  new ResizeObserver(resize).observe(canvas);
  resize();

  function clear() {
    for (const o of [mesh, lines, featureLines, openLines]) if (o) { model.remove(o); o.geometry.dispose(); }
    mesh = lines = featureLines = openLines = null;
    invalidate();
  }

  function setModel(geometry, edges, openEdges) {
    clear();
    if (!geometry) return;
    mesh = new THREE.Mesh(geometry, meshMat);
    model.add(mesh);
    let lg;
    if (edges) {
      lg = new THREE.BufferGeometry();
      lg.setAttribute('position', new THREE.BufferAttribute(edges, 3));
    } else {
      lg = new THREE.EdgesGeometry(geometry, 1);
    }
    lines = new THREE.LineSegments(lg, edgeMat);
    model.add(lines);
    if (openEdges && openEdges.length) {
      const og = new THREE.BufferGeometry();
      og.setAttribute('position', new THREE.BufferAttribute(openEdges, 3));
      openLines = new THREE.LineSegments(og, openMat);
      openLines.renderOrder = 10;
      model.add(openLines);
    }
    applyMode();
    fit();
  }

  function applyMode() {
    if (lines) lines.visible = mode === 'wire';
    if (mode === 'feature' && mesh && !featureLines) {
      featureLines = new THREE.LineSegments(new THREE.EdgesGeometry(mesh.geometry, 20), edgeMat);
      model.add(featureLines);
    }
    if (featureLines) featureLines.visible = mode === 'feature';
    invalidate();
  }

  function fit() {
    if (!mesh) return;
    const g = mesh.geometry;
    if (!g.boundingSphere) g.computeBoundingSphere();
    const c = g.boundingSphere.center, r = Math.max(g.boundingSphere.radius, 1e-3);
    const dist = r / Math.sin(THREE.MathUtils.degToRad(camera.fov / 2)) * 1.15;
    const dir = new THREE.Vector3(0.55, -0.7, 0.45).normalize();
    camera.position.copy(c).addScaledVector(dir, dist);
    camera.near = dist / 100;
    camera.far = dist * 20;
    camera.updateProjectionMatrix();
    controls.target.copy(c);
    controls.update();
    invalidate();
  }

  function setMode(m) {
    mode = m;
    applyMode();
  }

  function snapshot(w = 192, h = 144) {
    return new Promise((resolve) => {
      render();
      const out = document.createElement('canvas');
      out.width = w; out.height = h;
      const ctx = out.getContext('2d');
      ctx.fillStyle = '#141414';
      ctx.fillRect(0, 0, w, h);
      const sw = canvas.width, sh = canvas.height;
      const scale = Math.max(w / sw, h / sh);
      const dw = sw * scale, dh = sh * scale;
      ctx.drawImage(canvas, (w - dw) / 2, (h - dh) / 2, dw, dh);
      out.toBlob(resolve, 'image/png');
    });
  }

  return { setModel, setMode, fit, clear, snapshot, invalidate };
}

// Boundary edges of a triangle soup: edges referenced by exactly one triangle.
// ponytail: string-keyed hash, fine to ~1M triangles; skip above that.
export function openEdges(geometry) {
  const pos = geometry.attributes.position.array;
  const tri = pos.length / 9;
  if (tri > 1_000_000) return null;
  const vid = new Map();
  const ids = new Int32Array(tri * 3);
  for (let i = 0; i < tri * 3; i++) {
    const k = pos[i * 3].toFixed(5) + ',' + pos[i * 3 + 1].toFixed(5) + ',' + pos[i * 3 + 2].toFixed(5);
    let id = vid.get(k);
    if (id === undefined) { id = vid.size; vid.set(k, id); }
    ids[i] = id;
  }
  const count = new Map();
  for (let t = 0; t < tri; t++) {
    for (let e = 0; e < 3; e++) {
      const a = ids[t * 3 + e], b = ids[t * 3 + (e + 1) % 3];
      const k = a < b ? a * 4294967296 + b : b * 4294967296 + a;
      const v = count.get(k);
      count.set(k, v ? { n: v.n + 1, i: v.i } : { n: 1, i: t * 3 + e });
    }
  }
  const out = [];
  for (const { n, i } of count.values()) {
    if (n !== 1) continue;
    const t = Math.floor(i / 3), e = i % 3;
    const a = (t * 3 + e) * 3, b = (t * 3 + (e + 1) % 3) * 3;
    out.push(pos[a], pos[a + 1], pos[a + 2], pos[b], pos[b + 1], pos[b + 2]);
  }
  return out.length ? new Float32Array(out) : null;
}

function makeTriad() {
  const g = new THREE.Group();
  const axes = [[1, 0, 0, 0xe05a5a, 'X'], [0, 1, 0, 0x5ac46a, 'Y'], [0, 0, 1, 0x5a8ee0, 'Z']];
  for (const [x, y, z, color, label] of axes) {
    const geo = new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), new THREE.Vector3(x, y, z).multiplyScalar(1.6)]);
    g.add(new THREE.Line(geo, new THREE.LineBasicMaterial({ color })));
    const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: letterTexture(label, color), depthTest: false }));
    sp.position.set(x, y, z).multiplyScalar(2.1);
    sp.scale.set(0.7, 0.7, 1);
    g.add(sp);
  }
  return g;
}

function letterTexture(letter, color) {
  const c = document.createElement('canvas');
  c.width = c.height = 64;
  const ctx = c.getContext('2d');
  ctx.font = 'bold 44px system-ui, sans-serif';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillStyle = '#' + color.toString(16).padStart(6, '0');
  ctx.fillText(letter, 32, 34);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}
