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

const window = { devicePixelRatio: 1, addEventListener() {} };
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
  console, document, window,
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
    get AGENT() { return AGENT; },
    get selected() { return V.selected; },
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
  check(!el("leftPanel").hidden && !el("rightPanel").hidden, "both sidebars start open");
  t.setPanelClosed("left", true);
  check(el("leftPanel").hidden, "left specs panel closes");
  check(el("railSpecs").hidden === false, "specs reopen rail appears");
  check(el("layout").classList.contains("hide-left"), "layout drops the left column");
  check(stored.get("watchcad.panel.left") === "closed", "left panel state persists");
  t.setPanelClosed("right", true);
  check(el("rightPanel").hidden, "right copilot panel closes");
  check(el("railCopilot").hidden === false, "copilot reopen rail appears");
  check(el("layout").classList.contains("hide-right"), "layout drops the right column");
  t.restorePanels();
  check(el("leftPanel").hidden && el("rightPanel").hidden, "closed panels survive a reload");
  t.setPanelClosed("left", false);
  check(!el("leftPanel").hidden && el("railSpecs").hidden, "left panel reopens from its rail");
  check(!el("layout").classList.contains("hide-left"), "layout restores the left column");
  t.setPanelClosed("right", false);
  check(!el("rightPanel").hidden && el("railCopilot").hidden, "right panel reopens from its rail");

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

  console.log("copilot");
  check(t.CHAT.messages.length >= 1, "copilot has an opening message");
  check(el("chatLog").innerHTML.includes("Design copilot") === false, "chat log does not duplicate the panel title");
  const original = t.STATE.spec.data.case.diameter_mm;
  await t.runPrompt("Make it 40 mm titanium with a dive bezel");
  check(t.STATE.spec.data.case.diameter_mm === 40, `brief changed diameter to ${t.STATE.spec.data.case.diameter_mm}`);
  check(t.CHAT.messages.some((m) => m.role === "user"), "user message recorded");
  check(t.CHAT.messages.some((m) => m.role === "assistant" && m.patches?.length), "assistant returned change chips");
  check(el("chatLog").innerHTML.includes('data-undo="1"'), "undo button rendered");
  check(el("engineChip").textContent.length > 0, `engine chip: ${el("engineChip").textContent}`);

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

  console.log(failures.length ? `\n${failures.length} failure(s)` : "\nall checks passed");
  process.exit(failures.length ? 1 : 0);
})().catch((error) => {
  console.error("harness error:", error.stack || error);
  process.exit(1);
});
