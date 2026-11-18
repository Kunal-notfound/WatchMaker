/* Headless smoke harness for the watchcad studio (no DOM package required).

   Start the studio first, then run:

       python3 -m watchcad serve --port 8766
       node tests/ui_smoke.js

   Override WATCHCAD_BASE when using another host or port. */
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const BASE = process.env.WATCHCAD_BASE || "http://127.0.0.1:8766";
const realFetch = global.fetch;

class ClassList {
  constructor() { this.set = new Set(); }
  add(...names) { names.forEach((name) => this.set.add(name)); }
  remove(...names) { names.forEach((name) => this.set.delete(name)); }
  toggle(name, force) {
    const on = force === undefined ? !this.set.has(name) : Boolean(force);
    if (on) this.set.add(name); else this.set.delete(name);
    return on;
  }
  contains(name) { return this.set.has(name); }
}

class El {
  constructor(id = "") {
    this.id = id;
    this.innerHTML = "";
    this.textContent = "";
    this.value = "";
    this.checked = false;
    this.className = "";
    this.classList = new ClassList();
    this.dataset = {};
    this.style = {};
    this.clientWidth = 1200;
    this.clientHeight = 800;
    this.width = 1200;
    this.height = 800;
    this.scrollTop = 0;
    this.scrollHeight = 0;
    this.listeners = {};
    this.lastElementChild = { innerHTML: "" };
  }
  get options() {
    return [...this.innerHTML.matchAll(/<option value="([^"]+)"/g)]
      .map((match) => ({ value: match[1] }));
  }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  removeEventListener() {}
  setPointerCapture() {}
  releasePointerCapture() {}
  hasPointerCapture() { return false; }
  getBoundingClientRect() { return { left: 0, top: 0, width: this.clientWidth, height: this.clientHeight }; }
  getContext() { return ctx2d; }
  querySelector() { return null; }
  querySelectorAll() { return []; }
  closest() { return null; }
  contains() { return false; }
  matches() { return false; }
  focus() { this.focused = true; }
  click() {
    for (const fn of this.listeners.click || []) fn({ target: this, preventDefault() {} });
  }
}

const ctx2d = new Proxy({
  globalAlpha: 1, lineJoin: "round", fillStyle: "", strokeStyle: "", lineWidth: 1,
}, {
  get(target, key) {
    if (key in target) return target[key];
    return (...args) => args;
  },
  set(target, key, value) { target[key] = value; return true; },
});

const elements = new Map();
const el = (id) => {
  if (!elements.has(id)) elements.set(id, new El(id));
  return elements.get(id);
};
el("viewer");
el("loading");

const document = {
  activeElement: null,
  querySelector(selector) {
    if (selector.startsWith("#")) return el(selector.slice(1));
    return null;
  },
  querySelectorAll() { return []; },
  addEventListener() {},
  createElement() { return new El(); },
};

class FakeCustomEvent {
  constructor(type, options = {}) {
    this.type = type;
    this.detail = options.detail;
    this.defaultPrevented = false;
  }
  preventDefault() { this.defaultPrevented = true; }
}

const window = {
  devicePixelRatio: 1,
  listeners: new Map(),
  addEventListener(type, fn) {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set());
    this.listeners.get(type).add(fn);
  },
  removeEventListener(type, fn) { this.listeners.get(type)?.delete(fn); },
  dispatchEvent(event) {
    for (const fn of [...(this.listeners.get(event.type) || [])]) fn(event);
    return !event.defaultPrevented;
  },
};
const eventLog = [];
const watchedEvents = [
  "watchcad:scene-changed", "watchcad:model-url", "watchcad:viewer-model",
  "watchcad:viewer-stale", "watchcad:viewer-error", "watchcad:session-changed",
  "watchcad:visibility", "watchcad:zoom",
];
for (const type of watchedEvents) {
  window.addEventListener(type, (event) => {
    eventLog.push({ type, detail: event.detail ? { ...event.detail } : null, at: Date.now() });
  });
}

// The Three.js module cannot run in this DOM-less harness.  This minimal stand-in
// keeps the same revision/generation fences so the smoke test exercises the
// complete event path from a committed spec to a rendered-model acknowledgement.
const fakeViewer = { token: 0, sessions: new Map() };
function viewerStateFor(session) {
  if (!fakeViewer.sessions.has(session)) {
    // Generations are session-local: a lower generation from another design is
    // not an old model for that design.
    fakeViewer.sessions.set(session, { revision: 0, generation: 0 });
  }
  return fakeViewer.sessions.get(session);
}
async function simulateViewerLoad(url, expectedRevision) {
  const token = ++fakeViewer.token;
  let base = String(url || "");
  if (!base) base = `/api/model.glb?quality=high&revision=${expectedRevision}`;
  else if (!/[?&]revision=/.test(base)) {
    base += `${base.includes("?") ? "&" : "?"}revision=${expectedRevision}`;
  }
  const session = window.localStorage.getItem("watchcad.session") || "default";
  const state = viewerStateFor(session);
  try {
    const response = await realFetch(BASE + base, {
      headers: { "X-WatchCAD-Session": session },
      cache: "no-store",
    });
    if (token !== fakeViewer.token) return;
    if (!response.ok) return;
    const headerRevision = Number(response.headers.get("X-Design-Revision") || 0);
    const generation = Number(response.headers.get("X-Model-Generation") || 0);
    const preview = response.headers.get("X-Model-Preview") === "true";
    if (expectedRevision && headerRevision && headerRevision !== expectedRevision) {
      window.dispatchEvent(new FakeCustomEvent("watchcad:viewer-stale", {
        detail: { expected: expectedRevision, received: headerRevision },
      }));
      return;
    }
    if (generation && generation <= state.generation && !preview) return;
    await response.arrayBuffer();
    if (token !== fakeViewer.token) return;
    state.revision = headerRevision || state.revision;
    state.generation = generation || state.generation + 1;
    window.dispatchEvent(new FakeCustomEvent("watchcad:viewer-model", {
      detail: {
        session, preview,
        revision: state.revision, generation: state.generation,
      },
    }));
  } catch (error) {
    if (token !== fakeViewer.token) return;
    window.dispatchEvent(new FakeCustomEvent("watchcad:viewer-error", {
      detail: { message: String(error?.message || error) },
    }));
  }
}
window.addEventListener("watchcad:session-changed", () => {
  fakeViewer.token += 1;
});
window.addEventListener("watchcad:scene-changed", (event) => {
  simulateViewerLoad("", Number(event.detail?.revision || 0));
});
window.addEventListener("watchcad:model-url", (event) => {
  simulateViewerLoad(event.detail?.url, Number(event.detail?.revision || 0));
});

const stored = new Map();
window.localStorage = {
  getItem: (key) => (stored.has(key) ? stored.get(key) : null),
  setItem: (key, value) => stored.set(key, String(value)),
  removeItem: (key) => stored.delete(key),
};
class FakeFileReader {
  readAsDataURL(file) {
    this.result = file?.dataUrl || "data:image/png;base64,iVBORw0KGgo=";
    this.onload?.();
  }
}
const context = {
  console, document, window, CustomEvent: FakeCustomEvent,
  localStorage: window.localStorage,
  fetch: (path, options) => realFetch(BASE + path, options),
  setTimeout, clearTimeout, setInterval, clearInterval,
  requestAnimationFrame: (fn) => { fn(); return 1; },
  structuredClone, JSON, Math, Number, String, Array, Object, Float32Array, Uint32Array, Uint16Array, Set, Map,
  Blob: global.Blob,
  URL: global.URL,
  TextDecoder: global.TextDecoder,
  FileReader: FakeFileReader,
  confirm: () => true,
  alert: () => {},
};
context.globalThis = context;
context.self = context;
vm.createContext(context);

const appPath = path.join(__dirname, "..", "web", "app.js");
const source = fs.readFileSync(appPath, "utf8")
  + `\nglobalThis.__t = {
    get STATE() { return STATE; }, get SCHEMA() { return SCHEMA; },
    get CHAT() { return CHAT; }, get V() { return V; },
    boot, setView, setExplodeValue, zoomBy, runPrompt, undoPrompt,
    renderChat, setField, syncFormValues, isVisible,
    setPanelClosed, restorePanels, pickAt, selectPartAt, clearPartSelection,
    approveAgent, applyVariant, undoTurn, redoTurn, toggleAgentHistory,
    sendChat, newChat, loadConversations, toggleSettings, toggleChatHistory,
    deleteChatMessage, renderAttachments, attachImages,
    saveProvider, removeProvider, currentSession,
    runOptimisation, applyCandidate, onGlobalClick, renderOptimisation,
    previewCandidate, armCandidateApply, clearCandidatePreview,
    refreshCritic, setComponentFocus, renderCritic, applyCriticFix,
    setWorkspace, setDock, onGlobalKeydown, setTreeExpanded,
    selectComponent, setComponentVisibility, isolateComponent,
    toggleComponentLock, renderComponentTree, renderVerificationDock,
    openMovementModal, openHistoryModal, openCompareModal,
    createSnapshot, loadVersions, restoreVersion,
    get CRITIC() { return CRITIC; },
    get AGENT() { return AGENT; }, get DESIGN() { return DESIGN; },
    get TREE() { return TREE; }, get WORKSPACE() { return WORKSPACE; },
    get MOVEMENT() { return MOVEMENT; },
    get selected() { return V.selected; },
    get appliedRevision() { return appliedRevision; },
    get renderedRevision() { return renderedRevision; },
  };\n`;
vm.runInContext(source, context, { filename: "app.js" });

const failures = [];
const check = (condition, message) => {
  if (condition) console.log(`  ok   ${message}`);
  else { failures.push(message); console.log(`  FAIL ${message}`); }
};
const waitFor = async (fn, label, timeout = 15000) => {
  const started = Date.now();
  while (Date.now() - started < timeout) {
    try { if (fn()) return; } catch { /* not ready */ }
    await new Promise((resolve) => setTimeout(resolve, 40));
  }
  throw new Error(`timed out waiting for ${label}`);
};

(async () => {
  const t = context.__t;
  // The smoke harness must be deterministic: never contact a real model, even
  // if the developer's environment has one configured.
  if (el("useLlm")) el("useLlm").checked = false;
  await waitFor(() => t.STATE && t.V.mesh, "boot + mesh");
  console.log("boot");
  check(t.STATE.spec.data.case.diameter_mm > 0, "state loaded");
  check(t.V.mesh.triangles > 1000, `mesh loaded (${t.V.mesh.triangles} tris)`);
  check(t.V.mesh.bandCount >= 5, `vertical bands = ${t.V.mesh.bandCount}`);
  check(t.V.mesh.vertBand.length === t.V.mesh.pos.length / 3, "every vertex has a layer band");
  check(t.V.mesh.triBand.length === t.V.mesh.triangles, "every triangle has a layer band");
  check(t.V.mesh.triRange.length === t.V.mesh.triangles, "every triangle maps to a part");
  check(t.V.mesh.alphas.every((alpha) => alpha === 1), "all parts render opaque");
  check(t.V.mesh.selectedColors.length === t.V.mesh.triangles, "selection colors are precomputed");

  await waitFor(() => eventLog.some((entry) => entry.type === "watchcad:viewer-model"),
    "initial viewer model");
  const initialSceneEvent = eventLog.find((entry) => entry.type === "watchcad:scene-changed");
  const initialViewerEvent = eventLog.find((entry) => entry.type === "watchcad:viewer-model");
  check(Boolean(initialSceneEvent) && Number(initialSceneEvent.detail.revision) > 0,
    "scene-changed carries the committed revision");
  check(initialViewerEvent?.detail.revision === initialSceneEvent?.detail.revision,
    "viewer acknowledges the same revision the scene was built from");
  check(t.appliedRevision === t.renderedRevision && t.appliedRevision > 0,
    `applied and rendered revisions agree (${t.appliedRevision})`);
  check(!eventLog.some((entry) => entry.type === "watchcad:viewer-stale"),
    "no stale model was accepted during boot");

  console.log("component tree");
  check(t.TREE.components.length >= 10,
    `component registry loaded (${t.TREE.components.length} components)`);
  let treeHtml = el("componentTree").innerHTML;
  check(treeHtml.includes('data-tree-select="case"')
    && treeHtml.includes('data-tree-visibility="case"')
    && treeHtml.includes('data-tree-isolate="case"')
    && treeHtml.includes('data-tree-lock="case"'),
  "component rows expose select, visibility, isolate and lock actions");
  t.setTreeExpanded(true);
  treeHtml = el("componentTree").innerHTML;
  check(treeHtml.includes('data-tree-part="hour_hand"')
    && treeHtml.includes('data-tree-part-visibility="hour_hand"'),
  "expanded components expose selectable and hideable parts");
  t.TREE.query = "hour_hand";
  t.renderComponentTree();
  treeHtml = el("componentTree").innerHTML;
  check(treeHtml.includes('data-tree-select="hands"')
    && !treeHtml.includes('data-tree-select="case"'),
  "component search filters by part name");
  t.TREE.query = "";
  t.renderComponentTree();

  const hands = t.TREE.components.find((item) => item.id === "hands");
  await t.selectComponent("hands");
  check(t.V.selectedComponent === "hands"
    && t.V.highlightParts.has("hour_hand"),
  "selecting a component highlights its real scene parts");
  check(el("assistantFocus").hidden === false
    && el("assistantFocusName").textContent === "Hands",
  "component selection scopes the assistant");
  const visibilityEvents = eventLog.filter(
    (entry) => entry.type === "watchcad:visibility");
  check(visibilityEvents.length > 0
    && visibilityEvents[visibilityEvents.length - 1].detail.highlight.includes("hour_hand"),
  "component highlight is replayed through the visibility bridge");
  t.isolateComponent(hands);
  check(t.V.isolateParts instanceof Set && t.V.isolateParts.has("hour_hand")
    && !t.V.isolateParts.has("dial"),
  "component isolation is part-level and excludes other components");
  t.isolateComponent(hands);
  check(t.V.isolateParts === null, "component isolation toggles back off");
  t.setComponentVisibility(hands, false);
  check(hands.parts.every((part) => t.V.hiddenParts.has(part)),
    "component visibility hides every owned part");
  t.setComponentVisibility(hands, true);
  check(hands.parts.every((part) => !t.V.hiddenParts.has(part)),
    "component visibility restores every owned part");
  const locksBefore = (t.STATE.spec.locks || []).slice();
  await t.toggleComponentLock("dial");
  check(t.STATE.spec.locks.includes("dial.diameter_mm")
    && t.STATE.spec.locks.includes("dial.thickness_mm"),
  "component lock writes the authoritative dimension fields");
  await t.toggleComponentLock("dial");
  check(JSON.stringify(t.STATE.spec.locks) === JSON.stringify(locksBefore),
    "component lock toggles back to the original state");
  await t.setComponentFocus(null);

  console.log("verification dock");
  const dockHtml = el("verificationDock").innerHTML;
  for (const field of ["Confidence", "Component", "Reported", "Constraint",
    "Basis", "Corrective action"]) {
    check(dockHtml.includes(field), `verification reports ${field.toLowerCase()}`);
  }
  check(dockHtml.includes("PASS") && dockHtml.includes("NEEDS TEST"),
    "verdicts are labelled in text, not by colour alone");

  console.log("fields");
  const form = el("specForm").innerHTML;
  check(form.includes('data-number'), "numeric fields render number inputs");
  check(form.includes('class="stepper"'), "numeric fields render steppers");
  check(!form.includes("data-slider"), "no slider markup remains");
  check(!form.includes('type="range"'), "no range input remains in spec fields");

  console.log("explode");
  const before = t.V.dist;
  t.setExplodeValue(100, true);
  check(t.V.explode === 1, "explode reaches 100%");
  check(el("explodeValue").textContent === "100%", "explode label updates");
  check(t.V.dist !== before, "fit recalculates distance");
  check(/layers/.test(el("layerInfo").textContent), `layer readout: ${el("layerInfo").textContent}`);
  check(t.V.mesh.bandZ[t.V.mesh.bandCount - 1] > t.V.mesh.bandZ[0], "bands are sorted vertically");
  t.setExplodeValue(250, true);
  check(t.V.explode === 2.5, "explode extends to 250%");
  check(Number(el("explode").value) === 250, "explode slider reaches 250%");
  check(el("explodeValue").textContent === "250%", "explode label shows the extended range");
  t.setExplodeValue(100, true);

  console.log("views");
  t.setView("gears");
  check(t.V.isolate instanceof Set, "gears view isolates the movement");
  check(t.V.isolate.has("Movement") && t.V.isolate.has("Complications"), "gears view keeps movement + complications");
  check(t.V.explode >= 0.85, "gears view separates the gear layers");
  check(t.isVisible("Movement") && !t.isVisible("Dial"), "dial hidden in gears view");
  t.setView("layers");
  check(t.V.isolate === null, "layers view shows the full assembly");
  check(t.V.explode === 1, "layers view opens the stack fully");
  t.setView("reset");
  check(t.V.focus.every((value) => Number.isFinite(value)), "fit produces a finite focus point");

  console.log("picking");
  let hit = null, hitX = 0, hitY = 0;
  for (let y = 120; y < 700 && !hit; y += 18) {
    for (let x = 140; x < 1100; x += 18) {
      const candidate = t.pickAt(x, y);
      if (candidate) { hit = candidate; hitX = x; hitY = y; break; }
    }
  }
  check(Boolean(hit), `ray pick finds a part${hit ? ` (${hit.name})` : ""}`);
  if (hit) {
    t.selectPartAt(hitX, hitY);
    check(t.V.selected === hit.index, "click selects the picked part");
    check(el("partHud").hidden === false, "part readout appears");
    check(el("partName").textContent === hit.name, `part readout names ${hit.name}`);
    check(el("partMeta").textContent.includes("mm"), `part readout reports size: ${el("partMeta").textContent}`);
    t.clearPartSelection();
    check(t.V.selected === null && el("partHud").hidden === true, "selection can be cleared");
  }

  console.log("panels");
  const collapsed = (id) => el(id).classList.contains("panel-collapsed");
  check(!collapsed("leftPanel") && !collapsed("rightPanel"), "both sidebars start open");
  t.setPanelClosed("left", true);
  check(collapsed("leftPanel"), "left specs panel closes");
  check(el("railSpecs").hidden === false, "specs rail stays visible when closed");
  check(el("railSpecs").dataset.state === "closed", "specs rail marks the closed state");
  check(el("layout").classList.contains("hide-left"), "layout drops the left column");
  check(stored.get("watchcad.panel.left") === "closed", "left panel state persists");
  t.setPanelClosed("right", true);
  check(collapsed("rightPanel"), "right copilot panel closes");
  check(el("railCopilot").hidden === false, "AI agent rail stays visible when closed");
  check(el("railCopilot").dataset.state === "closed", "AI agent rail marks the closed state");
  check(el("layout").classList.contains("hide-right"), "layout drops the right column");
  t.restorePanels();
  check(collapsed("leftPanel") && collapsed("rightPanel"), "closed panels survive a reload");
  t.setPanelClosed("left", false);
  check(!collapsed("leftPanel"), "left panel reopens from its rail");
  check(el("railSpecs").hidden === false && el("railSpecs").dataset.state === "open",
    "specs rail stays visible when open");
  check(!el("layout").classList.contains("hide-left"), "layout restores the left column");
  t.setPanelClosed("right", false);
  check(!collapsed("rightPanel"), "right panel reopens from its rail");
  check(el("railCopilot").hidden === false && el("railCopilot").dataset.state === "open",
    "AI agent rail stays visible when open");

  // Delegated clicks: <body data-workspace> must not swallow every click.
  const fakeClick = (matches) => ({
    target: {
      dataset: {},
      closest(selector) {
        // Emulate the DOM: body is an ancestor matching [data-workspace],
        // but it is not a button.
        if (selector === "[data-workspace]") return { dataset: { workspace: "design" } };
        if (selector === "button[data-workspace]") return null;
        return matches[selector] || null;
      },
    },
  });
  const toggle = (side) => fakeClick({
    "[data-panel-toggle]": { dataset: { panelToggle: side } },
  });
  t.onGlobalClick(fakeClick({ "[data-close]": { dataset: { close: "right" } } }));
  check(collapsed("rightPanel"), "clicking the close button hides the assistant sidebar");
  check(el("railCopilot").hidden === false, "closing via click leaves the rail available");
  t.onGlobalClick(toggle("right"));
  check(!collapsed("rightPanel"), "clicking the AI agent tab reopens the assistant");
  t.onGlobalClick(toggle("right"));
  check(collapsed("rightPanel"), "clicking the AI agent tab closes it again");
  t.onGlobalClick(fakeClick({ "[data-close]": { dataset: { close: "left" } } }));
  check(collapsed("leftPanel"), "clicking the tab close hides the specs sidebar");
  t.onGlobalClick(toggle("left"));
  check(!collapsed("leftPanel"), "clicking the specs tab reopens the specs sidebar");
  t.onGlobalClick(toggle("left"));
  check(collapsed("leftPanel"), "clicking the specs tab closes it again");
  t.onGlobalClick(toggle("left"));
  t.onGlobalClick(toggle("right"));
  check(!collapsed("leftPanel") && !collapsed("rightPanel"),
    "both sidebars reopen for the remaining checks");

  console.log("workspaces and dock");
  const workspaceClick = (name) => ({
    target: {
      closest(selector) {
        return selector === "button[data-workspace]"
          ? { dataset: { workspace: name } } : null;
      },
    },
  });
  t.onGlobalClick(workspaceClick("optimise"));
  check(el("workspaceLabel").textContent === "Optimisation Lab"
    && el("workspaceHint").textContent.includes("nothing applies"),
  "optimisation workspace has its own contextual header");
  check(stored.get("watchcad.workspace") === "optimise",
    "workspace choice persists");
  t.onGlobalClick(workspaceClick("engineering"));
  check(el("workspaceLabel").textContent === "Engineering Studio"
    && el("engineeringToolStatus").textContent.includes("solver-backed"),
  "engineering workspace exposes solver-backed status");
  t.onGlobalClick(workspaceClick("design"));
  check(el("workspaceLabel").textContent === "Design Studio",
    "design workspace restores its contextual header");

  t.onGlobalClick(fakeClick({ "[data-dock]": { dataset: { dock: "timeline" } } }));
  check(t.WORKSPACE.dock === "timeline"
    && el("timelineDock").classList.contains("active")
    && stored.get("watchcad.dock") === "timeline",
  "bottom dock tabs switch and persist");
  t.onGlobalClick(fakeClick({ "[data-dock-toggle]": { dataset: {} } }));
  check(el("bottomDock").classList.contains("collapsed"),
    "bottom dock collapses without hiding the viewport");
  t.onGlobalClick(fakeClick({ "[data-dock-toggle]": { dataset: {} } }));
  check(!el("bottomDock").classList.contains("collapsed"),
    "bottom dock expands again");
  t.onGlobalClick(fakeClick({ "[data-dock]": { dataset: { dock: "verification" } } }));
  check(el("verificationDock").classList.contains("active"),
    "verification dock is restored for the remaining checks");

  console.log("zoom");
  const distance = t.V.dist;
  t.zoomBy(0.8);
  check(t.V.dist < distance, "zoom in reduces camera distance");
  t.zoomBy(1.25);
  check(Math.abs(t.V.dist - distance) < 1e-6, "zoom out restores the distance");
  for (let i = 0; i < 24; i += 1) t.zoomBy(0.8);
  check(t.V.dist < distance * 0.15, `zoom reaches a much closer range (${t.V.dist.toFixed(2)})`);
  check(t.V.dist <= t.V.focusRadius * 0.06 + 1e-6, "zoom clamps at the near limit");
  t.setView("reset");

  console.log("keyboard shortcuts");
  const keyEvent = (key, options = {}) => ({
    key,
    target: { closest: () => null },
    preventDefault() { this.defaultPrevented = true; },
    ctrlKey: false, metaKey: false, shiftKey: false, altKey: false,
    ...options,
  });
  t.onGlobalKeydown(keyEvent("2"));
  check(el("workspaceLabel").textContent === "Engineering Studio",
    "key 2 opens the engineering workspace");
  t.onGlobalKeydown(keyEvent("/"));
  check(el("componentSearch").focused === true,
    "slash focuses the component search");
  const keyDistance = t.V.dist;
  t.onGlobalKeydown(keyEvent("+"));
  check(t.V.dist < keyDistance, "plus zooms in");
  t.onGlobalKeydown(keyEvent("-"));
  check(Math.abs(t.V.dist - keyDistance) < 1e-6, "minus zooms out");
  await t.setComponentFocus({ name: "crown", role: "case", group: "Case" });
  t.onGlobalKeydown(keyEvent("Escape"));
  check(t.CRITIC.focus === null && el("focusChip").hidden,
    "escape clears the component scope and assistant focus");
  t.onGlobalKeydown(keyEvent("1"));

  console.log("copilot");
  check(t.CHAT.messages.length >= 1, "copilot has an opening message");
  check(el("chatLog").innerHTML.includes("Design copilot") === false, "chat log does not duplicate the panel title");
  const original = t.STATE.spec.data.case.diameter_mm;
  const revisionBeforePrompt = t.appliedRevision;
  await t.runPrompt("Make it 40 mm titanium with a dive bezel");
  check(t.STATE.spec.data.case.diameter_mm === 40, `brief changed diameter to ${t.STATE.spec.data.case.diameter_mm}`);
  check(t.CHAT.messages.some((m) => m.role === "user"), "user message recorded");
  check(t.CHAT.messages.some((m) => m.role === "assistant" && m.patches?.length), "assistant returned change chips");
  check(el("chatLog").innerHTML.includes('data-undo="1"'), "undo button rendered");
  check(el("engineChip").textContent.length > 0, `engine chip: ${el("engineChip").textContent}`);
  await waitFor(() => t.renderedRevision > revisionBeforePrompt
    && eventLog.some((entry) => entry.type === "watchcad:viewer-model"
      && entry.detail.revision === t.renderedRevision),
  "viewer renders the prompt revision");
  const promptScene = eventLog.find((entry) => entry.type === "watchcad:scene-changed"
    && entry.detail.revision === t.appliedRevision);
  const promptViewer = eventLog.find((entry) => entry.type === "watchcad:viewer-model"
    && entry.detail.revision === t.renderedRevision);
  check(Boolean(promptScene), "a committed prompt emits a new scene revision");
  check(promptViewer?.detail.revision === promptScene?.detail.revision,
    `prompt revision survives scene -> GLB -> viewer (r${promptViewer?.detail.revision})`);
  check(!eventLog.some((entry) => entry.type === "watchcad:viewer-stale"),
    "no stale model was accepted after the prompt");

  console.log("undo");
  await t.undoPrompt();
  check(t.STATE.spec.data.case.diameter_mm === original, "undo restores the previous diameter");
  check(t.CHAT.prevSpec === null, "undo stack is consumed");
  check(el("chatLog").innerHTML.includes("Reverted"), "undo is acknowledged in the chat");

  console.log("agent");
  const startColor = t.STATE.spec.data.dial.color;
  el("autonomy").value = "suggest";
  await t.runPrompt("make the dial blue");
  check(t.AGENT.result?.status === "suggested", `suggest status: ${t.AGENT.result?.status}`);
  check(t.STATE.spec.data.dial.color === startColor, "suggest mode does not write the design");
  check((t.AGENT.changes || []).some((change) => change.path === "dial.color"),
    "suggest exposes the validated diff");
  check(el("agentTimeline").innerHTML.includes("tl-kind"), "timeline rendered tool events");
  check(el("agentDiff").innerHTML.includes("dial.color"), "diff view names the changed field");
  check(el("agentStatus").innerHTML.includes("suggested"), "status strip reports the run");

  el("autonomy").value = "approval";
  await t.runPrompt("make the dial blue");
  check(t.AGENT.result?.status === "awaiting_approval", "approval run stages the change");
  check(Boolean(t.AGENT.pending?.token), "approval token exposed");
  check(el("agentApproval").hidden === false, "approval card is visible");
  await t.approveAgent(true);
  check(t.STATE.spec.data.dial.color === "blue", "approved change is applied");
  check(!t.AGENT.pending, "approval card clears after applying");

  el("autonomy").value = "suggest";
  await t.runPrompt("show me alternatives");
  check(t.AGENT.result?.intent === "variations", "variation intent detected");
  check((t.AGENT.variants || []).length > 0, "variants rendered");
  check(el("agentVariants").innerHTML.includes("data-variant"), "variant cards have an apply control");

  el("agentHistory").hidden = true;
  await t.toggleAgentHistory();
  check(Boolean(t.AGENT.history), "history endpoint loaded");
  check(el("agentHistory").innerHTML.includes("Design history"), "history panel rendered");

  await t.undoTurn();
  check(t.STATE.spec.data.dial.color === startColor, "agent undo restores the previous version");
  await t.redoTurn();
  check(t.STATE.spec.data.dial.color === "blue", "agent redo restores the approved version");

  console.log("chat composer");
  el("autonomy").value = "safe";
  await t.attachImages({ target: { value: "", files: [
    { name: "reference.png", size: 1024, dataUrl: "data:image/png;base64,iVBORw0KGgo=" },
  ] } });
  check(t.CHAT.attachments.length === 1, "image attachment is staged");
  check(el("attachmentStrip").hidden === false && el("attachmentStrip").innerHTML.includes("<img"),
    "attachment strip renders the preview");
  t.CHAT.attachments = [];
  t.renderAttachments();
  check(el("attachmentStrip").hidden === true, "attachment strip clears");

  await t.sendChat("Make the case 41 mm");
  check(Boolean(t.CHAT.conversationId), "chat created a persisted conversation");
  check(t.STATE.spec.data.case.diameter_mm === 41,
    `chat changed the diameter to ${t.STATE.spec.data.case.diameter_mm}`);
  check(t.CHAT.messages.some((message) => message.role === "user"),
    "chat recorded the user turn");
  const answer = t.CHAT.messages[t.CHAT.messages.length - 1];
  check(answer?.role === "assistant" && !answer.streaming, "SSE stream finished into an answer");
  check(el("chatLog").innerHTML.includes("41"), "streamed answer is in the chat log");
  check(el("chatStatus").hidden === true, "streaming status is cleared");
  check(t.CHAT.messages.some((message) => (message.patches || []).length),
    "chat shows validated change chips");

  await t.loadConversations();
  check((t.CHAT.conversations || []).some((row) => row.id === t.CHAT.conversationId),
    "conversation appears in the history sidebar");
  await t.toggleChatHistory();
  check(el("chatSidebar").hidden === false, "history sidebar opens");
  await t.toggleChatHistory();

  await t.toggleSettings();
  check(el("settingsBox").hidden === false, "settings panel opens");
  check(Boolean((el("settingsHint").textContent || "").trim()), "settings reports the model state");
  await t.toggleSettings();

  const firstUser = t.CHAT.messages.find((message) => message.role === "user");
  await t.sendChat("Make the case 40 mm", { editId: firstUser.id });
  check(t.STATE.spec.data.case.diameter_mm === 40, "edited message replaced the turn");
  check(t.CHAT.messages.length === 2, "edit left a single question and answer");

  await t.sendChat("", { regenerate: true });
  check(t.CHAT.messages.length === 2 && t.CHAT.messages[1].role === "assistant",
    "regenerate replaced the answer in place");

  const lastAnswer = t.CHAT.messages.length - 1;
  const lastId = t.CHAT.messages[lastAnswer].id;
  await t.deleteChatMessage(lastAnswer);
  check(Boolean(lastId) && t.CHAT.messages.length === 1 && t.CHAT.messages[0].role === "user",
    "message delete is persisted");

  const previousChat = t.CHAT.conversationId;
  await t.newChat();
  check(t.CHAT.conversationId && t.CHAT.conversationId !== previousChat,
    "new chat starts a separate conversation");
  check(t.STATE.spec.data.case.diameter_mm === 40, "new chat clones the current design");

  el("autonomy").value = "approval";
  await t.sendChat("Make the case 39 mm");
  check(t.STATE.spec.data.case.diameter_mm === 40, "chat approval stages without applying");
  check(Boolean(t.CHAT.pending?.token), "chat approval exposes a token");
  await t.approveAgent(true);
  check(t.STATE.spec.data.case.diameter_mm === 39, "chat approval applies after confirmation");
  check(!t.CHAT.pending, "chat approval clears after applying");

  console.log("critic");
  await t.setComponentFocus({ name: "crown", role: "case", group: "Case" });
  check(t.CRITIC.report?.axes?.length === 5, "critic returns five axes");
  check(t.CRITIC.report?.focus?.id === "crown", "critic is scoped to the selected component");
  check((t.CRITIC.context?.fields || []).some((field) => field.path === "case.crown"),
    "component context lists the crown fields");
  check(el("criticSummary").innerHTML.includes("critic-number"), "critic overall score renders");
  check(el("criticAxes").innerHTML.includes("Proportions"), "critic axes render");
  check(el("criticFocus").innerHTML.includes("Crown"), "component card renders the focus");
  check(!el("focusChip").hidden && el("focusName").textContent === "crown",
    "focus chip names the selected component");
  await t.setComponentFocus(null);
  check(t.CRITIC.report === null && el("focusChip").hidden,
    "clearing the selection clears the critic focus");

  console.log("optimisation");
  el("optimiseObjective").value = "weight";
  el("optimiseBudget").value = "6";
  await t.runOptimisation();
  const opt = t.DESIGN.optimisation;
  check(opt?.candidates?.length === 6, "optimisation returns the requested candidates");
  check(el("optimiseStatus").innerHTML.includes("6 candidates"), "lab reports the candidate count");
  check(el("candidateList").innerHTML.includes("data-apply-candidate"),
    "candidate cards render an apply control");
  const baselineCard = el("candidateList").innerHTML.split("</article>")
    .find((chunk) => chunk.includes('data-apply-candidate="baseline"'));
  check(Boolean(baselineCard) && baselineCard.includes("disabled"),
    "baseline card cannot be applied");
  const winner = (opt?.candidates || []).find((item) => item.id === opt.winner);
  check(Boolean(winner) && winner.status === "ok", `winner is solvable (${winner?.label})`);
  check(el("optimiseAxisX").options.length >= 5 && el("optimiseAxisY").options.length >= 5,
    "scatter axis selectors expose every objective");
  check(el("optimiseScatter").innerHTML.includes("opt-point"),
    "scatter renders candidate points");
  check(el("optimiseScatter").innerHTML.includes("frontier"),
    "scatter separates the Pareto frontier from dominated points");
  t.onGlobalClick(fakeClick({
    "[data-inspect-candidate]": { dataset: { inspectCandidate: winner.id } },
  }));
  check(el("candidateDetail").innerHTML.includes("Hard constraints"),
    "inspection shows hard constraints separately from objectives");
  check(el("candidateDetail").innerHTML.includes(winner.label),
    "inspection names the selected candidate");
  if (winner?.status === "ok" && winner.changes?.length) {
    t.armCandidateApply(winner.id);
    check(el("candidateDetail").innerHTML.includes("Confirm apply"),
      "applying requires an explicit confirmation step");
    const modelEventsBeforePreview = eventLog.filter(
      (entry) => entry.type === "watchcad:model-url").length;
    const previewSession = window.localStorage.getItem("watchcad.session") || "default";
    const generationBeforePreview = viewerStateFor(previewSession).generation;
    await t.previewCandidate(winner.id);
    check(Boolean(t.DESIGN.previewId) && el("btnClearPreview").hidden === false,
      "candidate preview loads a non-mutating 3D scene");
    await waitFor(() => eventLog.some((entry) => entry.type === "watchcad:viewer-model"
      && entry.detail.preview && entry.detail.revision === t.appliedRevision),
    "viewer renders the candidate preview");
    const previewUrlEvent = eventLog.filter(
      (entry) => entry.type === "watchcad:model-url").slice(modelEventsBeforePreview)
      .find((entry) => String(entry.detail.url).includes("preview="));
    const previewViewerEvent = eventLog.find((entry) => entry.type === "watchcad:viewer-model"
      && entry.detail.preview && entry.detail.revision === t.appliedRevision);
    check(previewUrlEvent?.detail.revision === t.appliedRevision,
      "preview model-url keeps the applied revision");
    check(previewViewerEvent?.detail.revision === previewUrlEvent?.detail.revision
      && previewViewerEvent?.detail.generation > generationBeforePreview,
    "preview revision and generation reach the viewer without mutating the design");

    t.clearCandidatePreview();
    check(!t.DESIGN.previewId, "clearing preview restores the live design view");
    await waitFor(() => eventLog.some((entry) => entry.type === "watchcad:viewer-model"
      && !entry.detail.preview && entry.detail.revision === t.appliedRevision
      && entry.detail.generation > previewViewerEvent.detail.generation),
    "viewer restores the live model after clearing the preview");
    const beforeChecksum = t.STATE.spec.data.derived.checksum;
    const applied = winner.applied || [];
    await t.applyCandidate(winner.id);
    const matches = applied.every((change) => {
      const actual = change.path.split(".")
        .reduce((node, key) => node?.[key], t.STATE.spec.data);
      return actual === change.to;
    });
    check(matches, "candidate changes reached the validated variant pipeline");
    check(t.STATE.spec.data.derived.checksum !== beforeChecksum,
      "applying a candidate re-solves the design");
    check(el("optimiseStatus").innerHTML.includes("Design changed"),
      "stale lab results warn after applying");
  }

  console.log("comparison and history dialogs");
  t.openCompareModal();
  const compareLabel = winner?.label || opt?.candidates?.[0]?.label || "";
  check(el("compareModal").hidden === false
    && el("candidateCompare").innerHTML.includes("compare-table")
    && el("candidateCompare").innerHTML.includes(compareLabel),
  "candidate comparison shows real solved candidates side by side");
  check(el("candidateCompare").innerHTML.includes("data-preview-candidate")
    && el("candidateCompare").innerHTML.includes("data-apply-candidate"),
  "comparison keeps preview and explicit apply actions separate");
  el("compareModal").hidden = true;

  t.openMovementModal();
  await waitFor(() => Boolean(t.MOVEMENT.catalog)
    && el("movementList").innerHTML.length > 0, "movement catalogue");
  check(el("movementModal").hidden === false
    && el("movementList").innerHTML.includes("data-movement"),
  "movement catalogue is loaded from the manufacturer data source");
  el("movementModal").hidden = true;

  const snapshotRevision = t.appliedRevision;
  const restoredColour = t.STATE.spec.data.dial.color;
  el("snapshotLabel").value = "Smoke snapshot";
  await t.createSnapshot();
  check(t.appliedRevision === snapshotRevision,
    "creating a snapshot does not create a new design revision");
  const snapshot = t.WORKSPACE.versions.find(
    (version) => version.label === "Smoke snapshot");
  check(Boolean(snapshot) && snapshot.kind === "snapshot",
    "snapshot is recorded in the version list");
  await t.setField("dial.color", "green");
  const beforeRestore = t.appliedRevision;
  await t.restoreVersion(snapshot.id);
  check(t.appliedRevision === beforeRestore + 1,
    "restoring a version commits one new revision");
  check(t.STATE.spec.data.dial.color === restoredColour,
    "restore returns the saved writable specification");
  check(t.STATE.spec.data.meta.version > 2
    && el("restoreConfirm").hidden === true,
  "restore increments the version counter and clears its confirmation");
  await waitFor(() => t.renderedRevision === t.appliedRevision,
    "viewer renders the restored revision");

  console.log("providers");
  const sid = t.currentSession();
  const sessionHeaders = { "X-WatchCAD-Session": sid };
  const settingsResponse = await realFetch(`${BASE}/api/settings`, { headers: sessionHeaders });
  const settingsBefore = (await settingsResponse.json()).settings || {};
  const secret = "sk-smoke-secret-do-not-leak";
  el("providerEndpoint").value = "https://example.invalid/v1";
  el("providerModel").value = "smoke-model";
  el("providerKey").value = secret;
  await t.saveProvider();
  check(el("providerStatus").textContent.includes("Saved"), "provider save is acknowledged");
  check(!el("providerStatus").textContent.includes(secret), "save status never echoes the key");
  check(!el("providerSecurity").innerHTML.includes(secret), "security panel never echoes the key");
  check(el("settingsHint").textContent.includes("never persisted"),
    "security hint explains key storage");
  check(el("providerKey").value === "", "key field clears after saving");
  check(el("useLlm").checked === true, "saving a key enables the model toggle");
  check(t.DESIGN.providerKey === true, "design state records a session key");
  check(t.CHAT.engine === "llm ready", "engine switches to llm ready");
  const settingsSaved = await (await realFetch(`${BASE}/api/settings`,
    { headers: sessionHeaders })).json();
  check(settingsSaved.settings.url === "https://example.invalid/v1",
    "non-secret endpoint persists in settings");
  check(!JSON.stringify(settingsSaved).includes(secret), "settings API never returns the key");

  await t.removeProvider();
  check(el("providerStatus").textContent.includes("removed"), "provider removal is acknowledged");
  check(el("useLlm").checked === false, "removing a key disables the model toggle");
  check(t.DESIGN.providerKey === false, "design state drops the session key");
  check(t.CHAT.engine === "local rules", "engine falls back to local rules");
  const settingsRemoved = await (await realFetch(`${BASE}/api/settings`,
    { headers: sessionHeaders })).json();
  check(!JSON.stringify(settingsRemoved).includes(secret), "removed key is not returned by the API");
  check(settingsRemoved.settings.url === "https://example.invalid/v1",
    "removal keeps the non-secret endpoint");

  // Restore the developer's local settings file; the smoke run must not leave
  // a fake endpoint behind for the next boot.
  const restore = {};
  for (const key of ["provider", "url", "model", "vision_model", "temperature",
    "max_tokens", "timeout_s", "retries", "retry_delay_s", "search_url",
    "search_provider"]) {
    restore[key] = settingsBefore[key] ?? "";
  }
  await realFetch(`${BASE}/api/settings`, {
    method: "POST",
    headers: { ...sessionHeaders, "Content-Type": "application/json" },
    body: JSON.stringify({ settings: restore }),
  });
  const settingsRestored = await (await realFetch(`${BASE}/api/settings`,
    { headers: sessionHeaders })).json();
  check(settingsRestored.settings.url !== "https://example.invalid/v1",
    "smoke run restores the previous settings");

  console.log(failures.length ? `\n${failures.length} failure(s)` : "\nall checks passed");
  process.exit(failures.length ? 1 : 0);
})().catch((error) => {
  console.error("harness error:", error.stack || error);
  process.exit(1);
});
