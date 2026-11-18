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

function envIntensity(role) {
  const scale = Number(QUALITY[S.quality]?.env ?? 1);
  const base = role === "dial" ? 0.7 : role === "crystal" ? 1.25 : 1.05;
  return base * scale;
}

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
  hiddenParts: new Set(),
  isolate: null,
  isolateParts: null,
  highlightParts: new Set(),
  selected: null,
  view: "three",
  frame: 0,
  token: 0,
  // The newest request always owns the token.  ``activeLoad`` is the only job
  // allowed to install; ``pendingLoad`` is the newest request that superseded
  // it, so a burst of edits coalesces into one final load.
  activeLoad: null,
  pendingLoad: null,
  expectedRevision: 0,
  expectedChecksum: "",
  expectedModificationId: "",
  expectedSession: "",
  expectedPreview: false,
  expectedPreviewId: "",
  loaded: {
    session: "", revision: 0, checksum: "", generation: 0,
    modificationId: "", preview: false, previewId: "",
  },
  lastEmbeddedIdentity: emptyIdentity(),
  loadStats: { started: 0, installed: 0, stale: 0, errors: 0, sessionResets: 0 },
  stats: {
    frames: 0, renders: 0, disposedGeometries: 0,
    disposedMaterials: 0, disposedTextures: 0,
  },
  listeners: [],
  resizeObserver: null,
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
    // app.js creates the browser session before /api/state arrives; using its
    // helper keeps the viewer header identical to every other API request.
    return window.currentSession?.()
      || window.localStorage?.getItem("watchcad.session") || "default";
  } catch {
    return "default";
  }
}

function emit(name, detail) {
  try { window.dispatchEvent(new CustomEvent(name, { detail })); } catch { /* no events */ }
}

/* Every listener the viewer owns is tracked so pagehide can remove all of them
   and browser tests can prove repeated model swaps do not accumulate any. */
function track(target, type, fn, options) {
  if (!target?.addEventListener) return;
  target.addEventListener(type, fn, options);
  S.listeners.push({ target, type, fn, options });
}

function untrackAll() {
  for (const entry of S.listeners) {
    try { entry.target.removeEventListener(entry.type, entry.fn, entry.options); }
    catch { /* already gone */ }
  }
  S.listeners.length = 0;
}

function listenerCounts() {
  const counts = {};
  for (const entry of S.listeners) counts[entry.type] = (counts[entry.type] || 0) + 1;
  return counts;
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
  controls.zoomToCursor = false;
  track(controls, "change", () => requestRender());

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
  requestRender(true);

  window.WatchCAD3D = {
    available: true,
    get ready() { return Boolean(S.root); },
    get scene() { return S.scene; },
    load: () => load(),
    reload: () => load(),
    setView: (name) => placeCamera(name, true),
    setExplode: (value) => setExplode(value),
    setVisibility: (state) => setVisibility(state),
    visibilityState: () => visibilityState(),
    selectPart: (name) => selectByName(name),
    clearSelection: () => selectByName(null),
    cameraState: () => cameraState(),
    projectPoint: (world) => projectPoint(world),
    pointerPoint: (clientX, clientY) => pointerPoint(clientX, clientY),
    zoomAt: (clientX, clientY, factor) => zoomAt(clientX, clientY, factor),
    setQuality: (name) => setQuality(name),
    setModelUrl: (url, meta) => {
      setExpectedFromDetail({ ...(meta || {}), session: meta?.session || sessionId() },
        String(url || ""));
      load();
    },
    get loadedRevision() { return S.loaded.revision; },
    get loadedGeneration() { return S.loaded.generation; },
    get identity() { return { ...S.loaded }; },
    get expected() {
      return {
        session: S.expectedSession, revision: S.expectedRevision,
        checksum: S.expectedChecksum, generation: S.loaded.generation,
        modificationId: S.expectedModificationId,
        preview: S.expectedPreview, previewId: S.expectedPreviewId,
      };
    },
    get loadStats() { return { ...S.loadStats }; },
    get activeLoads() { return S.activeLoad ? 1 : 0; },
    get pendingLoads() { return S.pendingLoad ? 1 : 0; },
    get disposed() { return S.disposed; },
    modelSnapshot: () => modelSnapshot(),
    resourceStats: () => resourceStats(),
    getQuality: () => S.quality,
    setBackground: (name) => setBackground(name),
    getBackground: () => S.background,
    presets: () => ({ quality: Object.keys(QUALITY), background: Object.keys(BACKGROUNDS) }),
    get wheelZoom() { return "pointer"; },
  };
  setStatus("3D viewer ready");
  return true;
}

function bindEvents() {
  const el = S.renderer.domElement;
  let down = null;
  track(el, "pointerdown", (event) => {
    down = { x: event.clientX, y: event.clientY, button: event.button };
  });
  track(el, "pointerup", (event) => {
    const start = down;
    down = null;
    if (!start || start.button !== 0) return;
    if (Math.hypot(event.clientX - start.x, event.clientY - start.y) > 4) return;
    pick(event.clientX, event.clientY);
  });
  track(el, "pointerleave", () => { down = null; });
  track(el, "dblclick", () => placeCamera("three", true));
  track(el, "contextmenu", (event) => event.preventDefault());
  // OrbitControls' own wheel dolly always orbits around the scene target.
  // Intercept mouse wheel in the capture phase and use the same pointer-plane
  // anchor as the 2D viewport; touch pinch is left to OrbitControls.
  track(el, "wheel", (event) => {
    event.preventDefault();
    event.stopImmediatePropagation();
    const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? el.clientHeight : 1;
    const delta = Number(event.deltaY) * unit;
    const factor = Math.max(0.5, Math.min(2, Math.exp(delta * 0.0012)));
    const rect = el.getBoundingClientRect();
    const x = Number.isFinite(event.clientX) ? event.clientX : rect.left + rect.width / 2;
    const y = Number.isFinite(event.clientY) ? event.clientY : rect.top + rect.height / 2;
    zoomAt(x, y, factor);
  }, { capture: true });
  track(el, "webglcontextlost", (event) => {
    event.preventDefault();
    document.body?.classList.remove("webgl3d");
    emit("watchcad:viewer-error", {
      message: "WebGL context lost; the software viewport is active again",
    });
  });
  track(window, "resize", () => resize());
  track(window, "pagehide", () => disposeAll());

  track(window, "watchcad:session-changed", (event) =>
    handleSessionChange(event.detail?.session));
  track(window, "watchcad:scene-changed", (event) => {
    setExpectedFromDetail(event.detail || {}, "");
    load();
  });
  track(window, "watchcad:model-url", (event) => {
    setExpectedFromDetail(event.detail || {},
      String(event.detail?.url || ""));
    load();
  });
  track(window, "watchcad:render-status", (event) => {
    const status = event.detail || {};
    if (status.status === "failed" && status.error) {
      setStatus(`render failed: ${String(status.error).slice(0, 120)}`);
    }
  });
  track(window, "watchcad:view", (event) => placeCamera(event.detail?.name, true));
  track(window, "watchcad:explode", (event) => setExplode(Number(event.detail?.value) || 0));
  track(window, "watchcad:zoom", (event) => zoom(Number(event.detail?.factor) || 1));
  track(window, "watchcad:visibility", (event) => setVisibility(event.detail || {}));
  track(window, "watchcad:select-part", (event) =>
    selectByName(event.detail?.name || null));
  track(window, "watchcad:clear-selection", () => selectByName(null));
  track(window, "watchcad:quality", (event) => setQuality(event.detail?.name));
  track(window, "watchcad:background", (event) => setBackground(event.detail?.name));

  if (window.ResizeObserver) {
    S.resizeObserver = new ResizeObserver(() => resize());
    S.resizeObserver.observe(el.parentElement || el);
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

/* Rendering is on demand: a frame is requested only while the camera is
   moving or a scene mutation marked the viewport dirty.  When both settle no
   animation frame is scheduled, so an idle tab costs nothing. */
function scheduleFrame() {
  if (S.disposed || S.frame || !S.renderer) return;
  S.frame = requestAnimationFrame(renderFrame);
}

function renderFrame() {
  S.frame = 0;
  if (S.disposed || !S.renderer) return;
  S.stats.frames += 1;
  const moved = Boolean(S.controls.update());
  if (moved || S.dirty) {
    S.dirty = false;
    S.renderer.render(S.scene, S.camera);
    S.stats.renders += 1;
  }
  if (moved || S.dirty) scheduleFrame();
}

function renderNow() {
  if (S.disposed || !S.renderer) return;
  S.stats.frames += 1;
  S.controls.update();
  S.dirty = false;
  S.renderer.render(S.scene, S.camera);
  S.stats.renders += 1;
}

function requestRender(force = false) {
  if (S.disposed) return;
  S.dirty = true;
  if (force) renderNow();
  else scheduleFrame();
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

function positiveInt(value) {
  const number = Number(value);
  return Number.isFinite(number) && number > 0 ? Math.floor(number) : 0;
}

function emptyIdentity(session = "") {
  return {
    session: String(session || ""), revision: 0, checksum: "", generation: 0,
    modificationId: "", preview: false, previewId: "",
  };
}

function publicJob(job) {
  return {
    session: job.session, revision: job.revision, checksum: job.checksum,
    modificationId: job.modificationId, preview: job.preview,
    previewId: job.previewId, generation: S.loaded.generation, token: job.token,
  };
}

function setExpectedFromDetail(detail, url) {
  S.modelUrl = String(url || "");
  S.expectedSession = String(detail.session || sessionId() || "default");
  S.expectedRevision = positiveInt(detail.revision);
  S.expectedChecksum = String(detail.checksum || "");
  S.expectedModificationId = String(detail.modification_id || detail.modificationId || "");
  S.expectedPreview = detail.preview === true || detail.preview === "true";
  S.expectedPreviewId = String(detail.preview_id || detail.previewId || "");
}

function invalidateLoads() {
  S.token += 1;
  if (S.activeLoad) {
    try { S.activeLoad.controller.abort(); } catch { /* already finished */ }
    S.activeLoad = null;
  }
  S.pendingLoad = null;
}

function handleSessionChange(session) {
  const next = String(session || sessionId() || "default");
  if (next === S.expectedSession && next === S.loaded.session) return;
  // A generation is only comparable inside its own session.  Drop the fence,
  // cancel every load from the previous session, but keep the last good root
  // on screen until the new session delivers a valid replacement.
  invalidateLoads();
  S.expectedSession = next;
  S.expectedRevision = 0;
  S.expectedChecksum = "";
  S.expectedModificationId = "";
  S.expectedPreview = false;
  S.expectedPreviewId = "";
  S.modelUrl = "";
  S.loaded = emptyIdentity(next);
  S.lastEmbeddedIdentity = emptyIdentity(next);
  S.loadStats.sessionResets += 1;
  S.tree = {};
  S.groups.clear();
  S.hidden = new Set();
  S.hiddenParts = new Set();
  S.isolate = null;
  S.isolateParts = null;
  S.highlightParts = new Set();
  S.selected = null;
  setStatus(`session ${next.slice(-8)} — loading design…`);
}

function load() {
  if (!S.renderer || S.disposed) return;
  // Every request gets a fresh token, even while another load is in flight.
  // The old fetch is aborted at once and cannot install or acknowledge anything.
  if (S.activeLoad) {
    try { S.activeLoad.controller.abort(); } catch { /* already finished */ }
    S.activeLoad = null;
  }
  const request = {
    token: ++S.token,
    session: S.expectedSession || sessionId(),
    modelUrl: S.modelUrl,
    revision: S.expectedRevision,
    checksum: S.expectedChecksum,
    modificationId: S.expectedModificationId,
    preview: S.expectedPreview,
    previewId: S.expectedPreviewId,
  };
  // A burst of edits overwrites the pending slot: only the newest one runs.
  S.pendingLoad = request;
  pumpLoads();
}

function pumpLoads() {
  if (S.activeLoad || !S.pendingLoad || S.disposed) return;
  const request = S.pendingLoad;
  S.pendingLoad = null;
  const job = { ...request, controller: new AbortController() };
  S.activeLoad = job;
  S.loadStats.started += 1;
  setStatus("loading 3D model…");
  void runLoad(job);
}

function isCurrent(job) {
  return !S.disposed && S.activeLoad === job && job.token === S.token;
}

function modelUrlFor(request) {
  const base = request.modelUrl
    || `/api/model.glb?quality=${encodeURIComponent(S.quality)}`;
  return request.revision && !/[?&]revision=/.test(base)
    ? `${base}${base.includes("?") ? "&" : "?"}revision=${request.revision}`
    : base;
}

function identityFromHeaders(headers) {
  return {
    session: String(headers?.get?.("X-WatchCAD-Session") || ""),
    revision: positiveInt(headers?.get?.("X-Design-Revision")),
    checksum: String(headers?.get?.("X-Design-Checksum") || ""),
    generation: positiveInt(headers?.get?.("X-Model-Generation")),
    modificationId: String(headers?.get?.("X-Modification-ID") || ""),
    preview: headers?.get?.("X-Model-Preview") === "true",
    previewId: String(headers?.get?.("X-Model-Preview-ID") || ""),
  };
}

function identityFromObject(value) {
  const source = value?.watchcad || value || {};
  return {
    session: String(source.session_id || source.session || ""),
    revision: positiveInt(source.revision),
    checksum: String(source.checksum || ""),
    generation: positiveInt(source.generation),
    modificationId: String(source.modification_id || source.modificationId || ""),
    preview: source.preview === true || source.preview === "true",
    previewId: String(source.preview_id || source.previewId || ""),
  };
}

function identityMismatch(actual, expected) {
  if (expected.session && actual.session !== expected.session) return "session";
  if (expected.revision && actual.revision !== expected.revision) return "revision";
  if (expected.checksum && actual.checksum !== expected.checksum) return "checksum";
  if (expected.modificationId
      && actual.modificationId !== expected.modificationId) return "modification";
  if (Boolean(actual.preview) !== Boolean(expected.preview)) return "preview";
  if (expected.previewId && actual.previewId !== expected.previewId) return "preview-id";
  if (!expected.preview && actual.previewId) return "preview-id";
  return "";
}

function generationMismatch(identity) {
  if (!identity.generation) return "missing-generation";
  if (S.loaded.session && identity.session !== S.loaded.session) return "session-fence";
  if (S.loaded.generation && identity.generation <= S.loaded.generation) {
    return "generation";
  }
  return "";
}

function emitStale(job, reason, received, source) {
  S.loadStats.stale += 1;
  emit("watchcad:viewer-stale", {
    reason, source, expected: publicJob(job), received: { ...received },
  });
}

function emitLoadError(job, error) {
  S.loadStats.errors += 1;
  const message = String(error?.message || error);
  console.warn("3D model load failed", error);
  setStatus(`3D model failed: ${message}`);
  emit("watchcad:viewer-error", {
    message, expected: publicJob(job),
  });
}

function embeddedIdentity(gltf) {
  const asset = gltf?.parser?.json?.asset || gltf?.asset || {};
  return identityFromObject(asset.extras);
}

async function runLoad(job) {
  try {
    const response = await fetch(modelUrlFor(job), {
      headers: { "X-WatchCAD-Session": job.session },
      signal: job.controller.signal,
      cache: "no-store",
    });
    if (!isCurrent(job)) return;
    if (!response.ok) throw new Error(`HTTP ${response.status}`);

    const identity = identityFromHeaders(response.headers);
    const mismatch = identityMismatch(identity, job);
    if (mismatch) {
      emitStale(job, `header-${mismatch}`, identity, "headers");
      return;
    }
    const generation = generationMismatch(identity);
    if (generation) {
      emitStale(job, generation, identity, "generation");
      return;
    }

    const buffer = await response.arrayBuffer();
    if (!isCurrent(job)) return;
    const gltf = await new Promise((resolve, reject) => {
      S.loader.parse(buffer, "", resolve, reject);
    });
    if (!isCurrent(job)) return;

    const embedded = embeddedIdentity(gltf);
    if (!embedded.session && !embedded.revision && !embedded.checksum) {
      emitLoadError(job, new Error("GLB is missing design identity metadata"));
      return;
    }
    const embeddedMismatch = identityMismatch(embedded, identity);
    if (embeddedMismatch) {
      emitStale(job, `glb-${embeddedMismatch}`, embedded, "glb");
      return;
    }

    // prepareModel builds and validates a complete replacement without touching
    // the current root, so a malformed GLB cannot blank the last good design.
    const prepared = prepareModel(gltf);
    if (!isCurrent(job)) { disposeObject(prepared.root); return; }
    install(prepared);
    await loadGroups(job, identity);
    if (!isCurrent(job)) return;
    commitLoaded(identity, embedded);
  } catch (error) {
    if (!isCurrent(job) || job.controller.signal.aborted) return;
    emitLoadError(job, error);
  } finally {
    if (S.activeLoad === job) {
      S.activeLoad = null;
      pumpLoads();
    }
  }
}

async function loadGroups(job, modelIdentity) {
  try {
    const base = `/api/scene?quality=${encodeURIComponent(S.quality)}`;
    const url = job.revision ? `${base}&revision=${job.revision}` : base;
    const response = await fetch(url, {
      headers: { "X-WatchCAD-Session": job.session },
      signal: job.controller.signal,
      cache: "no-store",
    });
    if (!isCurrent(job)) return;
    if (!response.ok) return;
    const headerIdentity = identityFromHeaders(response.headers);
    const headerMismatch = identityMismatch(headerIdentity, modelIdentity);
    if (headerMismatch) {
      emitStale(job, `scene-header-${headerMismatch}`, headerIdentity, "scene-headers");
      return;
    }
    const payload = await response.json();
    if (!isCurrent(job)) return;
    const payloadIdentity = identityFromObject(payload);
    const payloadMismatch = identityMismatch(payloadIdentity, modelIdentity);
    if (payloadMismatch) {
      emitStale(job, `scene-${payloadMismatch}`, payloadIdentity, "scene");
      return;
    }
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
    if (!isCurrent(job) || job.controller.signal.aborted) return;
    console.warn("group metadata unavailable", error);
  }
}

function geometryFingerprint(geometry) {
  const attribute = geometry?.getAttribute?.("position");
  if (!attribute) return "none:0";
  const stride = Math.max(1, Math.floor(attribute.count / 256));
  let hash = 2166136261;
  for (let index = 0; index < attribute.count; index += stride) {
    for (let axis = 0; axis < 3; axis += 1) {
      hash ^= Math.round(attribute.getComponent(index, axis) * 1000);
      hash = Math.imul(hash, 16777619);
    }
  }
  return `${(hash >>> 0).toString(16).padStart(8, "0")}:${attribute.count}`;
}

function boxData(box) {
  if (!box || (typeof box.isEmpty === "function" && box.isEmpty())) return null;
  const round = (value) => Math.round(value * 1000) / 1000;
  return {
    min: box.min.toArray().map(round),
    max: box.max.toArray().map(round),
    size: box.getSize(new THREE.Vector3()).toArray().map(round),
  };
}

function prepareModel(gltf) {
  const root = gltf.scene || gltf.scenes?.[0];
  if (!root) throw new Error("glTF contained no scene");
  const parts = [];
  const box = new THREE.Box3();
  root.updateMatrixWorld(true);
  try {
    root.traverse((child) => {
      if (!child.isMesh) return;
      const role = child.userData?.role || child.parent?.userData?.role || "part";
      const sourceMaterials = Array.isArray(child.material)
        ? child.material : [child.material];
      const material = makeMaterial(child.material, role);
      child.material = material;
      for (const source of sourceMaterials) {
        if (source && source !== material) source.dispose?.();
      }
      child.castShadow = role !== "crystal";
      child.receiveShadow = true;
      child.userData.role = role;
      child.userData.baseZ = 0;
      child.geometry?.computeBoundingBox?.();
      child.userData.geometryBounds = boxData(child.geometry?.boundingBox);
      child.userData.fingerprint = geometryFingerprint(child.geometry);
      parts.push(child);
      box.expandByObject(child);
    });
    if (!parts.length || box.isEmpty()) throw new Error("glTF contained no meshes");
    const center = box.getCenter(new THREE.Vector3());
    const radius = box.getSize(new THREE.Vector3()).length() / 2 || 25;
    root.position.set(-center.x, -center.y, -center.z);
    root.updateMatrixWorld(true);
    for (const part of parts) {
      const partBox = new THREE.Box3().setFromObject(part);
      part.userData.explodeCenter = partBox.getCenter(new THREE.Vector3())
        .sub(root.position);
    }
    return { root, parts, bounds: box.clone(), radius };
  } catch (error) {
    disposeObject(root);
    throw error;
  }
}

function install(model) {
  const preserveCamera = Boolean(S.root);
  if (S.root) {
    S.scene.remove(S.root);
    disposeObject(S.root);
    S.root = null;
  }
  S.root = model.root;
  S.parts = model.parts;
  S.bounds = model.bounds;
  S.radius = model.radius;
  S.center.set(0, 0, 0);
  S.scene.add(model.root);
  installShadowPlane();
  aimKeyLight();
  applyVisibility();
  applyExplode();
  if (preserveCamera) requestRender(true);
  else placeCamera(S.view, true);
}

function commitLoaded(identity, embedded) {
  S.loaded = { ...identity };
  S.lastEmbeddedIdentity = { ...embedded };
  S.loadStats.installed += 1;
  setStatus(`${S.parts.length} parts · PBR / IBL · r${S.loaded.revision}`);
  emit("watchcad:viewer-model", {
    parts: S.parts.length,
    ...{ ...identity },
  });
}

function modelSnapshot() {
  return {
    ready: Boolean(S.root),
    session: S.loaded.session,
    revision: S.loaded.revision,
    generation: S.loaded.generation,
    checksum: S.loaded.checksum,
    modificationId: S.loaded.modificationId,
    preview: S.loaded.preview,
    previewId: S.loaded.previewId,
    identity: { ...S.loaded },
    embeddedIdentity: { ...S.lastEmbeddedIdentity },
    expected: {
      session: S.expectedSession, revision: S.expectedRevision,
      checksum: S.expectedChecksum, modificationId: S.expectedModificationId,
      preview: S.expectedPreview, previewId: S.expectedPreviewId,
    },
    bounds: boxData(S.bounds),
    visibility: visibilityState(),
    parts: S.parts.map((part) => ({
      name: part.name,
      role: part.userData.role || "part",
      vertices: part.geometry?.getAttribute?.("position")?.count || 0,
      fingerprint: part.userData.fingerprint || "",
      bounds: part.userData.geometryBounds || boxData(part.geometry?.boundingBox),
      color: part.material?.color?.getHexString?.() || "",
      materialType: part.material?.type || "",
      visible: Boolean(part.visible),
      group: groupOf(part),
    })),
    loadStats: { ...S.loadStats },
    activeLoads: S.activeLoad ? 1 : 0,
    pendingLoads: S.pendingLoad ? 1 : 0,
  };
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
      envMapIntensity: envIntensity(role),
    });
  }

  const material = new THREE.MeshStandardMaterial({
    color: base,
    metalness: Math.min(1, Math.max(0, metalness)),
    roughness: Math.min(1, Math.max(0.02, roughness)),
    opacity: Math.min(1, Math.max(0.05, opacity)),
    transparent: opacity < 0.999,
    envMapIntensity: envIntensity(role),
  });
  const kind = String(extras.texture || "none");
  const seed = textureSeed(extras, role, kind);
  if (kind !== "none" && S.quality !== "low") {
    material.bumpMap = patternTexture(kind, QUALITY[S.quality]?.texture || 256, seed);
    material.bumpScale = kind === "grain" ? 0.035 : kind === "weave" ? 0.05
      : role === "dial" ? 0.02 : 0.012;
    material.needsUpdate = true;
  }
  material.userData.sourceExtras = extras;
  return material;
}

/* Deterministic noise helpers.  A texture is keyed by the component/material
   identity exported in glTF extras, so the same design always produces the
   same surface and a material change produces a different one. */
function hashSeed(text) {
  let hash = 2166136261;
  for (let i = 0; i < String(text).length; i += 1) {
    hash ^= String(text).charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

function seededRandom(seed) {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x6D2B79F5) >>> 0;
    let value = state;
    value = Math.imul(value ^ (value >>> 15), value | 1);
    value ^= value + Math.imul(value ^ (value >>> 7), value | 61);
    return ((value ^ (value >>> 14)) >>> 0) / 4294967296;
  };
}

function textureSeed(extras, role, kind) {
  const identity = extras.texture_seed || [
    role, kind, extras.material_key, extras.watch_material, extras.finish,
  ].filter(Boolean).join("|");
  return hashSeed(identity || "watchcad");
}

/* Procedural finish textures.  There are no downloaded image maps: the pattern
   is generated on a canvas from the material/finish metadata the server put in
   the glTF extras.  This is a visual approximation of brushed, radial, grain,
   weave and guilloché finishes — not a measured surface. */
function patternTexture(kind, size, seed = 1) {
  const key = `${kind}:${size}:${seed}`;
  if (S.textureCache.has(key)) return S.textureCache.get(key);
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = size;
  const ctx = canvas.getContext("2d");
  if (!ctx) return null;
  const random = seededRandom(hashSeed(`${seed}:${kind}:${size}`));
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
      const v = 128 + (random() - 0.5) * 110;
      pixels.data[i] = pixels.data[i + 1] = pixels.data[i + 2] = v;
    }
    ctx.putImageData(pixels, 0, 0);
  } else { // brushed
    for (let y = 0; y < size; y += 2) {
      const v = 150 + Math.round((random() - 0.5) * 70);
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
        ? patternTexture(kind, QUALITY[S.quality]?.texture || 256,
          textureSeed(extras, part.userData?.role || "part", kind)) : null;
      material.envMapIntensity = envIntensity(part.userData?.role || "part");
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
    disposeObject(S.shadowPlane);
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

function groupOf(part) {
  return part.userData.group || S.groups.get(part.name) || "Parts";
}

function setFromArray(value, current) {
  if (Array.isArray(value)) return new Set(value);
  if (value instanceof Set) return new Set(value);
  return current;
}

function setVisibility(state = {}) {
  S.hidden = setFromArray(state.hidden, S.hidden);
  S.hiddenParts = setFromArray(state.parts_hidden, S.hiddenParts);
  S.highlightParts = setFromArray(state.highlight, S.highlightParts);
  if (Array.isArray(state.isolate) || state.isolate instanceof Set) {
    S.isolate = setFromArray(state.isolate, null);
  } else if (Object.prototype.hasOwnProperty.call(state, "isolate")) {
    S.isolate = null;
  }
  if (Array.isArray(state.parts_isolate) || state.parts_isolate instanceof Set) {
    S.isolateParts = setFromArray(state.parts_isolate, null);
  } else if (Object.prototype.hasOwnProperty.call(state, "parts_isolate")) {
    S.isolateParts = null;
  }
  applyVisibility();
}

function applyVisibility() {
  for (const part of S.parts) {
    const group = groupOf(part);
    const groupVisible = !S.hidden.has(group) && (!S.isolate || S.isolate.has(group));
    const partVisible = !S.hiddenParts.has(part.name)
      && (!S.isolateParts || S.isolateParts.has(part.name));
    part.visible = groupVisible && partVisible;
    part.userData.dimmed = Boolean((S.isolate && !S.isolate.has(group))
      || (S.isolateParts && !S.isolateParts.has(part.name)));
  }
  applyEmissive();
  requestRender(true);
}

function visibilityState() {
  return {
    hidden: [...S.hidden],
    isolate: S.isolate ? [...S.isolate] : null,
    partsHidden: [...S.hiddenParts],
    partsIsolate: S.isolateParts ? [...S.isolateParts] : null,
    highlight: [...S.highlightParts],
    selected: S.selected,
  };
}

function cameraState() {
  if (!S.camera || !S.controls) return null;
  const position = S.camera.position.clone();
  const target = S.controls.target.clone();
  return {
    position: position.toArray(),
    target: target.toArray(),
    distance: position.distanceTo(target),
    fov: S.camera.fov,
    aspect: S.camera.aspect,
    minDistance: S.controls.minDistance,
    maxDistance: S.controls.maxDistance,
  };
}

function pointerRay(clientX, clientY) {
  if (!S.camera || !S.renderer) return null;
  const rect = S.renderer.domElement.getBoundingClientRect();
  const x = Number.isFinite(clientX) ? clientX : rect.left + rect.width / 2;
  const y = Number.isFinite(clientY) ? clientY : rect.top + rect.height / 2;
  const pointer = new THREE.Vector2(
    ((x - rect.left) / Math.max(1, rect.width)) * 2 - 1,
    -((y - rect.top) / Math.max(1, rect.height)) * 2 + 1,
  );
  S.camera.updateMatrixWorld();
  const ray = new THREE.Raycaster();
  ray.setFromCamera(pointer, S.camera);
  return ray.ray;
}

function targetPlane() {
  if (!S.camera || !S.controls) return null;
  const normal = new THREE.Vector3();
  S.camera.getWorldDirection(normal);
  return new THREE.Plane().setFromNormalAndCoplanarPoint(normal, S.controls.target);
}

function pointerPoint(clientX, clientY) {
  const ray = pointerRay(clientX, clientY);
  const plane = targetPlane();
  if (!ray || !plane) return null;
  const point = new THREE.Vector3();
  return ray.intersectPlane(plane, point) ? point.toArray() : null;
}

function projectPoint(world) {
  if (!S.camera || !S.renderer || !world) return null;
  const vector = Array.isArray(world)
    ? new THREE.Vector3(Number(world[0]) || 0, Number(world[1]) || 0, Number(world[2]) || 0)
    : new THREE.Vector3(Number(world.x) || 0, Number(world.y) || 0, Number(world.z) || 0);
  S.camera.updateMatrixWorld();
  const projected = vector.project(S.camera);
  const rect = S.renderer.domElement.getBoundingClientRect();
  return {
    x: rect.left + ((projected.x + 1) / 2) * rect.width,
    y: rect.top + ((1 - projected.y) / 2) * rect.height,
    ndc: [projected.x, projected.y],
    depth: projected.z,
  };
}

function zoomAt(clientX, clientY, factor) {
  if (!S.camera || !S.controls) return cameraState();
  const before = pointerPoint(clientX, clientY);
  const offset = S.camera.position.clone().sub(S.controls.target);
  const distance = Math.max(S.controls.minDistance,
    Math.min(S.controls.maxDistance,
      offset.length() * Math.max(0.01, Number(factor) || 1)));
  offset.setLength(distance);
  S.camera.position.copy(S.controls.target).add(offset);
  S.camera.lookAt(S.controls.target);
  S.camera.updateMatrixWorld();

  // Normalise the dolly through OrbitControls first (damping, clamps and
  // orientation), then apply the pointer-plane correction.  Applying the
  // correction before update() lets the controls' spherical pass shift the
  // anchor slightly, so the order here is deliberate.
  S.controls.update();
  if (before) {
    const afterPoint = new THREE.Vector3();
    const afterRay = pointerRay(clientX, clientY);
    const afterPlane = targetPlane();
    if (afterRay && afterRay.intersectPlane(afterPlane, afterPoint)) {
      const shift = new THREE.Vector3(...before).sub(afterPoint);
      S.camera.position.add(shift);
      S.controls.target.add(shift);
      S.camera.lookAt(S.controls.target);
      S.camera.updateMatrixWorld();
    }
  }
  requestRender(true);
  return cameraState();
}

function zoom(factor) {
  if (!S.camera || !S.controls) return;
  const rect = S.renderer.domElement.getBoundingClientRect();
  zoomAt(rect.left + rect.width / 2, rect.top + rect.height / 2, factor);
}

function setExplode(value) {
  S.explode = Math.max(0, Math.min(2.5, Number(value) || 0));
  applyExplode();
}

function applyExplode() {
  for (const part of S.parts) {
    const center = part.userData.explodeCenter?.z ?? 0;
    part.position.z = center * S.explode * 0.9;
  }
  requestRender(true);
}

function applyEmissive() {
  for (const part of S.parts) {
    const material = part.material;
    if (!material?.emissive) continue;
    if (!material.userData.baseEmissive) {
      material.userData.baseEmissive = material.emissive.clone();
    }
    if (S.selected && part.name === S.selected) material.emissive.setHex(0x7a5a18);
    else if (S.highlightParts.has(part.name)) material.emissive.setHex(0x35506d);
    else material.emissive.copy(material.userData.baseEmissive);
  }
}

function selectByName(name) {
  S.selected = name || null;
  applyEmissive();
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
  object?.traverse?.((child) => {
    if (!child.isMesh) return;
    child.geometry?.dispose?.();
    S.stats.disposedGeometries += 1;
    const materials = Array.isArray(child.material) ? child.material : [child.material];
    for (const material of materials) {
      // Cached procedural textures are shared by later models; disposing a
      // material must never dispose those textures.
      material?.dispose?.();
      S.stats.disposedMaterials += 1;
    }
  });
}

function resourceStats() {
  const geometries = new Set();
  const materials = new Set();
  const textures = new Set();
  S.scene?.traverse((child) => {
    if (!child.isMesh) return;
    if (child.geometry) geometries.add(child.geometry);
    const list = Array.isArray(child.material) ? child.material : [child.material];
    for (const material of list) {
      if (!material) continue;
      materials.add(material);
      for (const value of Object.values(material)) {
        if (value?.isTexture) textures.add(value);
      }
    }
  });
  const cached = [...S.textureCache.values()];
  return {
    geometries: geometries.size,
    materials: materials.size,
    textures: textures.size,
    textureCache: cached.length,
    cachedTexturesReferenced: cached.filter((texture) => textures.has(texture)).length,
    rendererGeometries: S.renderer?.info?.memory?.geometries ?? 0,
    rendererTextures: S.renderer?.info?.memory?.textures ?? 0,
    renderCalls: S.renderer?.info?.render?.calls ?? 0,
    frames: S.stats.frames,
    renders: S.stats.renders,
    disposedGeometries: S.stats.disposedGeometries,
    disposedMaterials: S.stats.disposedMaterials,
    disposedTextures: S.stats.disposedTextures,
    listeners: listenerCounts(),
    activeLoads: S.activeLoad ? 1 : 0,
    pendingLoads: S.pendingLoad ? 1 : 0,
    framePending: Boolean(S.frame),
    disposed: S.disposed,
  };
}

function disposeAll() {
  if (S.disposed) return;
  S.disposed = true;
  invalidateLoads();
  if (S.frame && window.cancelAnimationFrame) window.cancelAnimationFrame(S.frame);
  S.frame = 0;
  if (S.root) {
    S.scene?.remove(S.root);
    disposeObject(S.root);
    S.root = null;
    S.parts = [];
  }
  if (S.shadowPlane) {
    S.scene?.remove(S.shadowPlane);
    disposeObject(S.shadowPlane);
    S.shadowPlane = null;
  }
  for (const texture of S.textureCache.values()) {
    texture?.dispose?.();
    S.stats.disposedTextures += 1;
  }
  S.textureCache.clear();
  S.keyLight?.shadow?.map?.dispose?.();
  if (S.keyLight?.shadow) S.keyLight.shadow.map = null;
  S.env?.dispose?.();
  try { S.resizeObserver?.disconnect?.(); } catch { /* detached */ }
  S.resizeObserver = null;
  untrackAll();
  S.controls?.dispose?.();
  S.renderer?.dispose?.();
  S.scene?.clear?.();
}

if (init()) {
  load();
}

export { S as viewerState };
