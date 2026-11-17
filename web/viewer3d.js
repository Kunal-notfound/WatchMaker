/* watchcad 3D viewer — Three.js, physically based materials and IBL.
 *
 * The viewer is an ES module so the classic app.js (and its headless smoke
 * harness) keeps working without a module loader.  It renders the same solved
 * scene the server exports as glTF: every part, colour and material role comes
 * from /api/model.glb, so the picture is a projection of structured design
 * data — not a generated image.
 *
 * Performance: capped device pixel ratio, a single directional shadow map,
 * on-demand rendering while the camera is still, and no post-processing.
 * If WebGL is unavailable the module leaves the existing 2D canvas untouched.
 */
import * as THREE from "./vendor/three.module.js";
import { OrbitControls } from "./vendor/addons/controls/OrbitControls.js";
import { GLTFLoader } from "./vendor/addons/loaders/GLTFLoader.js";
import { RoomEnvironment } from "./vendor/addons/environments/RoomEnvironment.js";

const canvas = document.getElementById("viewer3d");

function stored(key, fallback) {
  try { return window.localStorage?.getItem(key) || fallback; } catch { return fallback; }
}

const QUALITY = {
  low: { pixelRatio: 1.0, shadows: false, shadowMap: 512, texture: 0, env: 0.85, exposure: 1.0 },
  medium: { pixelRatio: 1.35, shadows: true, shadowMap: 1024, texture: 256, env: 1.0, exposure: 1.03 },
  high: { pixelRatio: 1.9, shadows: true, shadowMap: 2048, texture: 512, env: 1.15, exposure: 1.06 },
};

const BACKGROUNDS = {
  studio: { color: null, css: "", note: "studio" },
  light: { color: 0xe9edf2, css: "", note: "light" },
  dark: { color: 0x14181d, css: "", note: "dark" },
  blueprint: { color: 0x0d2137, css: "", note: "blueprint" },
};

const S = {
  renderer: null,
  scene: null,
  camera: null,
  controls: null,
  root: null,
  env: null,
  loader: new GLTFLoader(),
  parts: [],
  groups: new Map(),
  tree: {},
  bounds: null,
  center: new THREE.Vector3(),
  radius: 25,
  explode: 0,
  hidden: new Set(),
  isolate: null,
  selected: null,
  view: "three",
  frame: 0,
  token: 0,
  loading: false,
  disposed: false,
  shadowPlane: null,
  dirty: true,
  quality: stored("watchcad.renderQuality", "high"),
  background: stored("watchcad.background", "studio"),
  textureCache: new Map(),
  modelUrl: "",
};

if (!QUALITY[S.quality]) S.quality = "high";
if (!BACKGROUNDS[S.background]) S.background = "studio";

function sessionId() {
  try {
    return window.localStorage?.getItem("watchcad.session") || "default";
  } catch {
    return "default";
  }
}

function emit(name, detail) {
  try { window.dispatchEvent(new CustomEvent(name, { detail })); } catch { /* no events */ }
}

function setStatus(text) {
  const node = document.getElementById("viewerInfo");
  if (node) node.textContent = text;
}

function init() {
  if (!canvas) return false;
  let renderer;
  try {
    renderer = new THREE.WebGLRenderer({
      canvas,
      antialias: true,
      alpha: true,
      powerPreference: "high-performance",
    });
  } catch (error) {
    console.warn("WebGL unavailable, keeping the 2D renderer", error);
    window.WatchCAD3D = { available: false, reason: String(error?.message || error) };
    return false;
  }

  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1,
    QUALITY[S.quality]?.pixelRatio || 1.9));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = QUALITY[S.quality]?.exposure || 1.05;
  renderer.shadowMap.enabled = Boolean(QUALITY[S.quality]?.shadows);
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  renderer.setClearColor(0x000000, 0);

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(32, 1, 0.4, 4000);
  camera.up.set(0, 0, 1);

  const controls = new OrbitControls(camera, canvas);
  controls.enableDamping = true;
  controls.dampingFactor = 0.075;
  controls.rotateSpeed = 0.62;
  controls.zoomSpeed = 0.8;
  controls.panSpeed = 0.7;
  controls.minDistance = 6;
  controls.maxDistance = 900;
  controls.screenSpacePanning = true;
  controls.addEventListener("change", () => requestRender());

  const key = new THREE.DirectionalLight(0xfff3df, 3.1);
  key.position.set(-60, -70, 110);
  key.castShadow = true;
  key.shadow.mapSize.set(QUALITY[S.quality]?.shadowMap || 1024,
    QUALITY[S.quality]?.shadowMap || 1024);
  key.shadow.bias = -0.0008;
  key.shadow.normalBias = 0.4;
  scene.add(key);
  scene.add(key.target);

  const rim = new THREE.DirectionalLight(0xbfd6ff, 1.25);
  rim.position.set(70, 40, 55);
  scene.add(rim);

  const fill = new THREE.HemisphereLight(0xeaf2ff, 0x59616b, 0.9);
  scene.add(fill);

  const pmrem = new THREE.PMREMGenerator(renderer);
  pmrem.compileEquirectangularShader();
  const envScene = new RoomEnvironment();
  const env = pmrem.fromScene(envScene, 0.04);
  scene.environment = env.texture;
  pmrem.dispose();
  envScene.dispose?.();

  S.renderer = renderer;
  S.scene = scene;
  S.camera = camera;
  S.controls = controls;
  S.keyLight = key;
  S.env = env;
  S.scene.background = BACKGROUNDS[S.background]?.color != null
    ? new THREE.Color(BACKGROUNDS[S.background].color) : null;

  document.body.classList.add("webgl3d");
  bindEvents();
  resize();
  placeCamera("three", true);
  animate();

  window.WatchCAD3D = {
    available: true,
    get ready() { return Boolean(S.root); },
    get scene() { return S.scene; },
    load: () => load(),
    reload: () => load(),
    setView: (name) => placeCamera(name, true),
    setExplode: (value) => setExplode(value),
    setVisibility: (state) => setVisibility(state),
    selectPart: (name) => selectByName(name),
    clearSelection: () => selectByName(null),
    setQuality: (name) => setQuality(name),
    setModelUrl: (url) => { S.modelUrl = String(url || ""); load(); },
    getQuality: () => S.quality,
    setBackground: (name) => setBackground(name),
    getBackground: () => S.background,
    presets: () => ({ quality: Object.keys(QUALITY), background: Object.keys(BACKGROUNDS) }),
  };
  setStatus("3D viewer ready");
  return true;
}

function bindEvents() {
  const el = S.renderer.domElement;
  let down = null;
  el.addEventListener("pointerdown", (event) => {
    down = { x: event.clientX, y: event.clientY, button: event.button };
  });
  el.addEventListener("pointerup", (event) => {
    const start = down;
    down = null;
    if (!start || start.button !== 0) return;
    if (Math.hypot(event.clientX - start.x, event.clientY - start.y) > 4) return;
    pick(event.clientX, event.clientY);
  });
  el.addEventListener("pointerleave", () => { down = null; });
  el.addEventListener("dblclick", () => placeCamera("three", true));
  el.addEventListener("contextmenu", (event) => event.preventDefault());
  el.addEventListener("webglcontextlost", (event) => {
    event.preventDefault();
    document.body?.classList.remove("webgl3d");
    emit("watchcad:viewer-error", {
      message: "WebGL context lost; the software viewport is active again",
    });
  });
  window.addEventListener("resize", () => resize());

  window.addEventListener("watchcad:scene-changed", () => { S.modelUrl = ""; load(); });
  window.addEventListener("watchcad:model-url", (event) => {
    S.modelUrl = String(event.detail?.url || "");
    load();
  });
  window.addEventListener("watchcad:view", (event) => placeCamera(event.detail?.name, true));
  window.addEventListener("watchcad:explode", (event) => setExplode(Number(event.detail?.value) || 0));
  window.addEventListener("watchcad:zoom", (event) => zoom(Number(event.detail?.factor) || 1));
  window.addEventListener("watchcad:visibility", (event) => setVisibility(event.detail || {}));
  window.addEventListener("watchcad:select-part", (event) =>
    selectByName(event.detail?.name || null));
  window.addEventListener("watchcad:clear-selection", () => selectByName(null));
  window.addEventListener("watchcad:quality", (event) => setQuality(event.detail?.name));
  window.addEventListener("watchcad:background", (event) => setBackground(event.detail?.name));

  if (window.ResizeObserver) {
    new ResizeObserver(() => resize()).observe(el.parentElement || el);
  }
}

function resize() {
  if (!S.renderer) return;
  const parent = canvas.parentElement;
  const width = Math.max(1, parent?.clientWidth || canvas.clientWidth || 800);
  const height = Math.max(1, parent?.clientHeight || canvas.clientHeight || 600);
  S.renderer.setSize(width, height, false);
  S.camera.aspect = width / height;
  S.camera.updateProjectionMatrix();
  requestRender(true);
}

function animate() {
  if (S.disposed) return;
  S.frame = requestAnimationFrame(animate);
  const moved = S.controls.update();
  if (moved || S.dirty) {
    S.dirty = false;
    S.renderer.render(S.scene, S.camera);
  }
}

function requestRender(force = false) {
  S.dirty = true;
  if (force && S.renderer) {
    S.controls.update();
    S.renderer.render(S.scene, S.camera);
  }
}

function placeCamera(name, apply) {
  const key = String(name || "three").toLowerCase();
  S.view = key;
  if (!apply || !S.camera) return;
  if (key === "reset") {
    const direction = S.camera.position.clone().sub(S.controls.target).normalize();
    S.controls.target.copy(S.center);
    S.camera.position.copy(S.center).add(direction.multiplyScalar(fitDistance() * 1.02));
    S.controls.update();
    requestRender(true);
    return;
  }
  let az = -0.62;
  let el = 0.82;
  let dist = fitDistance() * 1.02;
  if (key === "dial") { az = 0; el = Math.PI / 2 - 0.001; }
  else if (key === "crown") { az = Math.PI / 2; el = 0.12; dist *= 0.92; }
  else if (key === "gears") { az = -0.9; el = 1.02; }
  else if (key === "layers") { az = 0.05; el = 0.16; dist *= 1.06; }
  // Visibility and explode are driven by the app's separate events so the
  // Three.js view stays in lockstep with the 2D fallback.
  const target = S.center;
  S.controls.target.copy(target);
  S.camera.position.set(
    target.x + dist * Math.sin(az) * Math.cos(el),
    target.y - dist * Math.cos(az) * Math.cos(el),
    target.z + dist * Math.sin(el),
  );
  S.camera.lookAt(target);
  S.controls.update();
  requestRender(true);
}

function fitDistance() {
  const fov = (S.camera?.fov || 32) * Math.PI / 180;
  return Math.max(12, (S.radius || 25) / Math.sin(fov / 2) * 1.18);
}

async function load() {
  if (!S.renderer) return;
  if (S.loading) { S.pending = true; return; }
  S.loading = true;
  const token = ++S.token;
  setStatus("loading 3D model…");
  try {
    const url = S.modelUrl
      || `/api/model.glb?quality=${encodeURIComponent(S.quality)}`;
    const response = await fetch(url, {
      headers: { "X-WatchCAD-Session": sessionId() },
      cache: "no-store",
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const buffer = await response.arrayBuffer();
    if (token !== S.token) return;

    const gltf = await new Promise((resolve, reject) => {
      S.loader.parse(buffer, "", resolve, reject);
    });
    if (token !== S.token) return;
    install(gltf);
    await loadGroups();
    setStatus(`${S.parts.length} parts · PBR / IBL`);
    emit("watchcad:viewer-model", { parts: S.parts.length });
  } catch (error) {
    console.warn("3D model load failed", error);
    setStatus(`3D model failed: ${error.message || error}`);
    emit("watchcad:viewer-error", { message: String(error?.message || error) });
  } finally {
    S.loading = false;
    if (S.pending) { S.pending = false; load(); }
  }
}

function install(gltf) {
  if (S.root) {
    S.scene.remove(S.root);
    disposeObject(S.root);
  }
  const root = gltf.scene || gltf.scenes?.[0];
  if (!root) throw new Error("glTF contained no scene");
  S.root = root;
  S.parts = [];
  const box = new THREE.Box3();
  root.updateMatrixWorld(true);

  root.traverse((child) => {
    if (!child.isMesh) return;
    const role = child.userData?.role || child.parent?.userData?.role || "part";
    const material = makeMaterial(child.material, role);
    child.material = material;
    child.castShadow = role !== "crystal";
    child.receiveShadow = true;
    child.userData.role = role;
    child.userData.baseZ = 0;
    S.parts.push(child);
    box.expandByObject(child);
  });

  if (box.isEmpty()) throw new Error("glTF contained no meshes");
  S.bounds = box;
  const center = box.getCenter(new THREE.Vector3());
  S.radius = box.getSize(new THREE.Vector3()).length() / 2 || 25;

  // Centre the assembly on the origin so orbiting feels natural.  The camera
  // and all explode offsets work in this centred frame.
  root.position.set(-center.x, -center.y, -center.z);
  root.updateMatrixWorld(true);
  S.center.set(0, 0, 0);

  // Per-part centres in the root's local frame (translate by the root offset).
  for (const part of S.parts) {
    const partBox = new THREE.Box3().setFromObject(part);
    part.userData.explodeCenter = partBox.getCenter(new THREE.Vector3()).sub(root.position);
  }

  S.scene.add(root);
  installShadowPlane();
  aimKeyLight();
  applyVisibility();
  applyExplode();
  placeCamera(S.view, true);
}

function makeMaterial(source, role) {
  const extras = source?.userData || {};
  const base = source?.color?.clone?.() || new THREE.Color(0xb9c2cc);
  const metalness = Number.isFinite(source?.metalness) ? source.metalness
    : ["case", "hand", "part", "movement", "complication"].includes(role) ? 0.88 : 0.28;
  const roughness = Number.isFinite(source?.roughness) ? source.roughness
    : role === "dial" ? 0.52 : 0.28;
  const opacity = Number.isFinite(source?.opacity) ? source.opacity : 1.0;
  const transmission = Number(extras.transmission || 0);

  if (role === "crystal" || transmission > 0) {
    return new THREE.MeshPhysicalMaterial({
      color: base,
      metalness: 0.0,
      roughness: Math.min(0.18, roughness || 0.045),
      transmission: Math.max(0.55, transmission || 0.72),
      thickness: 0.7,
      ior: Number(extras.ior || 1.52),
      transparent: true,
      opacity: Math.min(0.8, opacity || 0.46),
      clearcoat: Number(extras.clearcoat || 1),
      clearcoatRoughness: 0.03,
      side: THREE.DoubleSide,
      depthWrite: false,
      envMapIntensity: 1.25,
    });
  }

  const material = new THREE.MeshStandardMaterial({
    color: base,
    metalness: Math.min(1, Math.max(0, metalness)),
    roughness: Math.min(1, Math.max(0.02, roughness)),
    opacity: Math.min(1, Math.max(0.05, opacity)),
    transparent: opacity < 0.999,
    envMapIntensity: role === "dial" ? 0.7 : 1.05,
  });
  const kind = String(extras.texture || "none");
  if (kind !== "none" && S.quality !== "low") {
    material.bumpMap = patternTexture(kind, QUALITY[S.quality]?.texture || 256);
    material.bumpScale = kind === "grain" ? 0.035 : kind === "weave" ? 0.05
      : role === "dial" ? 0.02 : 0.012;
    material.needsUpdate = true;
  }
  material.userData.sourceExtras = extras;
  return material;
}

/* Procedural finish textures.  There are no downloaded image maps: the pattern
   is generated on a canvas from the material/finish metadata the server put in
   the glTF extras.  This is a visual approximation of brushed, radial, grain,
   weave and guilloché finishes — not a measured surface. */
function patternTexture(kind, size) {
  const key = `${kind}:${size}`;
  if (S.textureCache.has(key)) return S.textureCache.get(key);
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = size;
  const ctx = canvas.getContext("2d");
  if (!ctx) return null;
  ctx.fillStyle = "#808080";
  ctx.fillRect(0, 0, size, size);
  ctx.strokeStyle = "rgba(255,255,255,0.55)";
  ctx.globalAlpha = 0.7;
  if (kind === "radial" || kind === "guilloche") {
    const center = size / 2;
    if (kind === "radial") {
      for (let i = 0; i < size; i += 2) {
        const a = (i / size) * Math.PI * 2;
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(center, center);
        ctx.lineTo(center + Math.cos(a) * center, center + Math.sin(a) * center);
        ctx.stroke();
      }
    } else {
      for (let r = 2; r < center; r += 3) {
        ctx.lineWidth = r % 6 ? 1 : 2;
        ctx.beginPath();
        ctx.arc(center, center, r, 0, Math.PI * 2);
        ctx.stroke();
      }
    }
  } else if (kind === "weave") {
    const cell = Math.max(4, size / 16);
    for (let y = 0; y < size; y += cell) {
      for (let x = 0; x < size; x += cell) {
        ctx.fillStyle = (x / cell + y / cell) % 2 ? "rgba(255,255,255,0.5)"
          : "rgba(0,0,0,0.4)";
        ctx.fillRect(x, y, cell, cell);
      }
    }
  } else if (kind === "grain") {
    const pixels = ctx.getImageData(0, 0, size, size);
    for (let i = 0; i < pixels.data.length; i += 4) {
      const v = 128 + (Math.random() - 0.5) * 110;
      pixels.data[i] = pixels.data[i + 1] = pixels.data[i + 2] = v;
    }
    ctx.putImageData(pixels, 0, 0);
  } else { // brushed
    for (let y = 0; y < size; y += 2) {
      const v = 150 + Math.round((Math.random() - 0.5) * 70);
      ctx.strokeStyle = `rgb(${v},${v},${v})`;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(0, y + 0.5);
      ctx.lineTo(size, y + 0.5);
      ctx.stroke();
    }
  }
  const texture = new THREE.CanvasTexture(canvas);
  texture.wrapS = texture.wrapT = THREE.RepeatWrapping;
  texture.repeat.set(kind === "radial" || kind === "guilloche" ? 1 : 3,
    kind === "radial" || kind === "guilloche" ? 1 : 3);
  texture.colorSpace = THREE.NoColorSpace;
  texture.anisotropy = Math.min(8, S.renderer?.capabilities?.getMaxAnisotropy?.() || 1);
  S.textureCache.set(key, texture);
  return texture;
}

function applyQualityMaterials() {
  for (const part of S.parts) {
    const material = part.material;
    const extras = material?.userData?.sourceExtras || {};
    const kind = String(extras.texture || "none");
    if (material?.isMeshStandardMaterial) {
      material.bumpMap = kind !== "none" && S.quality !== "low"
        ? patternTexture(kind, QUALITY[S.quality]?.texture || 256) : null;
      material.needsUpdate = true;
    }
  }
}

function setQuality(name) {
  const key = String(name || "").toLowerCase();
  if (!QUALITY[key]) return S.quality;
  S.quality = key;
  try { window.localStorage?.setItem("watchcad.renderQuality", key); } catch { /* private mode */ }
  const preset = QUALITY[key];
  if (S.renderer) {
    S.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, preset.pixelRatio));
    S.renderer.toneMappingExposure = preset.exposure;
    S.renderer.shadowMap.enabled = preset.shadows;
  }
  if (S.keyLight?.shadow) {
    S.keyLight.castShadow = preset.shadows;
    S.keyLight.shadow.mapSize.set(preset.shadowMap, preset.shadowMap);
    S.keyLight.shadow.map?.dispose?.();
    S.keyLight.shadow.map = null;
  }
  applyQualityMaterials();
  resize();
  load();
  emit("watchcad:quality-changed", { quality: key });
  return key;
}

function setBackground(name) {
  const key = String(name || "").toLowerCase();
  const preset = BACKGROUNDS[key] || BACKGROUNDS.studio;
  S.background = BACKGROUNDS[key] ? key : "studio";
  try { window.localStorage?.setItem("watchcad.background", S.background); } catch { /* private mode */ }
  if (S.scene) S.scene.background = preset.color === null ? null : new THREE.Color(preset.color);
  if (S.shadowPlane?.material) {
    S.shadowPlane.material.opacity = key === "dark" || key === "blueprint" ? 0.28 : 0.16;
  }
  requestRender(true);
  emit("watchcad:background-changed", { background: S.background });
  return S.background;
}

function installShadowPlane() {
  if (S.shadowPlane) {
    S.scene.remove(S.shadowPlane);
    S.shadowPlane.geometry.dispose();
    S.shadowPlane.material.dispose();
  }
  const size = S.radius * 6;
  const plane = new THREE.Mesh(
    new THREE.PlaneGeometry(size, size),
    new THREE.ShadowMaterial({ color: 0x1b2430, opacity: 0.16 }),
  );
  plane.position.set(0, 0, -S.radius * 1.25);
  plane.receiveShadow = true;
  S.scene.add(plane);
  S.shadowPlane = plane;
}

function aimKeyLight() {
  const key = S.keyLight;
  if (!key) return;
  const r = Math.max(20, S.radius);
  key.position.set(-r * 1.8, -r * 2.1, r * 3.2);
  key.target.position.set(0, 0, 0);
  key.target.updateMatrixWorld();
  const cam = key.shadow.camera;
  cam.left = -r * 1.6;
  cam.right = r * 1.6;
  cam.top = r * 1.6;
  cam.bottom = -r * 1.6;
  cam.near = 1;
  cam.far = r * 9;
  cam.updateProjectionMatrix();
}

async function loadGroups() {
  try {
    const response = await fetch(`/api/scene?quality=${encodeURIComponent(S.quality)}`, {
      headers: { "X-WatchCAD-Session": sessionId() },
      cache: "no-store",
    });
    if (!response.ok) return;
    const payload = await response.json();
    S.tree = payload.tree || {};
    S.groups.clear();
    for (const [group, names] of Object.entries(S.tree)) {
      for (const name of names) S.groups.set(name, group);
    }
    for (const part of S.parts) {
      if (!part.userData.group) part.userData.group = S.groups.get(part.name) || "Parts";
    }
    applyVisibility();
  } catch (error) {
    console.warn("group metadata unavailable", error);
  }
}

function groupOf(part) {
  return part.userData.group || S.groups.get(part.name) || "Parts";
}

function setVisibility(state = {}) {
  if (Array.isArray(state.hidden)) S.hidden = new Set(state.hidden);
  else if (state.hidden instanceof Set) S.hidden = new Set(state.hidden);
  S.isolate = Array.isArray(state.isolate) ? new Set(state.isolate)
    : state.isolate instanceof Set ? new Set(state.isolate) : null;
  applyVisibility();
}

function applyVisibility() {
  for (const part of S.parts) {
    const group = groupOf(part);
    part.visible = !S.hidden.has(group) && (!S.isolate || S.isolate.has(group));
    part.userData.dimmed = Boolean(S.isolate && !S.isolate.has(group));
  }
  requestRender(true);
}

function setExplode(value) {
  S.explode = Math.max(0, Math.min(2.5, Number(value) || 0));
  applyExplode();
}

function zoom(factor) {
  if (!S.camera || !S.controls) return;
  const offset = S.camera.position.clone().sub(S.controls.target);
  const distance = Math.max(S.controls.minDistance,
    Math.min(S.controls.maxDistance, offset.length() * factor));
  offset.setLength(distance);
  S.camera.position.copy(S.controls.target).add(offset);
  S.controls.update();
  requestRender(true);
}

function applyExplode() {
  for (const part of S.parts) {
    const center = part.userData.explodeCenter?.z ?? 0;
    part.position.z = center * S.explode * 0.9;
  }
  requestRender(true);
}

function selectByName(name) {
  for (const part of S.parts) {
    const material = part.material;
    if (!material) continue;
    if (!material.userData.baseEmissive) {
      material.userData.baseEmissive = material.emissive?.clone?.() || null;
    }
    if (material.emissive) {
      if (name && part.name === name) material.emissive.setHex(0x7a5a18);
      else if (material.userData.baseEmissive) material.emissive.copy(material.userData.baseEmissive);
      else material.emissive.setHex(0x000000);
    }
  }
  S.selected = name || null;
  requestRender(true);
}

function pick(clientX, clientY) {
  if (!S.parts.length) return;
  const rect = S.renderer.domElement.getBoundingClientRect();
  const pointer = new THREE.Vector2(
    ((clientX - rect.left) / rect.width) * 2 - 1,
    -((clientY - rect.top) / rect.height) * 2 + 1,
  );
  const ray = new THREE.Raycaster();
  ray.setFromCamera(pointer, S.camera);
  const hits = ray.intersectObjects(S.parts.filter((part) => part.visible), false);
  const hit = hits.find((item) => item.object.visible);
  if (!hit) {
    selectByName(null);
    emit("watchcad:select-part", null);
    return;
  }
  const part = hit.object;
  selectByName(part.name);
  emit("watchcad:select-part", {
    name: part.name,
    role: part.userData.role || "part",
    group: groupOf(part),
    point: [hit.point.x, hit.point.y, hit.point.z],
  });
}

function disposeObject(object) {
  object.traverse((child) => {
    if (!child.isMesh) return;
    child.geometry?.dispose?.();
    const materials = Array.isArray(child.material) ? child.material : [child.material];
    for (const material of materials) material?.dispose?.();
  });
}

if (init()) {
  load();
}

export { S as viewerState };
