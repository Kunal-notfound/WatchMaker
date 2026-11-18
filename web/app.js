/* watchcad studio — the browser only edits the spec and projects triangles.
   Every dimension comes from the server's deterministic solver. */

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

let SCHEMA = null;
let STATE = null;
let IDEAS = {};
const CHAT = {
  messages: [],
  typing: false,
  prevSpec: null,
  engine: "local rules",
  conversationId: null,
  conversations: [],
  attachments: [],
  edits: [],
  editId: "",
  runId: "",
  streaming: false,
  status: "",
  pending: null,
  historyOpen: false,
  settingsOpen: false,
};
const AGENT = {
  running: false,
  runId: null,
  sessionId: null,
  engine: "local rules",
  events: [],
  result: null,
  changes: [],
  variants: [],
  pending: null,
  history: null,
  error: null,
};
const DESIGN = {
  providers: [],
  provider: null,
  optimisation: null,
  optimiseToken: 0,
  optimiseBusy: false,
  optimiseAxes: ["weight", "cost"],
  selectedCandidate: null,
  pendingApply: null,
  previewId: "",
  previewToken: 0,
  capabilities: {},
  capabilityKey: "",
  providerKey: false,
};
const CRITIC = {
  report: null,
  context: null,
  focus: null,
  loading: false,
  error: "",
};
const TREE = {
  components: [],
  checkOwners: new Map(),
  query: "",
  selected: null,
  expanded: new Set(),
  loading: true,
  error: "",
};
const WORKSPACE = {
  dock: "verification",
  dockCollapsed: false,
  versions: [],
  historyVersion: null,
  restoreId: null,
  compare: new Set(),
};
const MOVEMENT = {
  catalog: null,
  list: [],
  detail: null,
  proposal: null,
  loading: false,
  error: "",
};
const DIAL_SWATCHES = [
  ["black", "#14171c"], ["blue", "#1c3f6e"], ["green", "#204436"],
  ["white", "#e9e6dd"], ["silver", "#c6c8cb"], ["salmon", "#c98d72"],
  ["burgundy", "#5a1f2a"], ["champagne", "#d8c49a"],
];
const AGENT_MODES = ["suggest", "approval", "safe", "autonomous"];
let activeTab = "spec";
let checkFilter = "all";
let sceneToken = 0;
let sceneTimer = 0;
let lastSceneSig = "";
let toastTimer = 0;
let appliedRevision = 0;
let renderedRevision = 0;
const REVISIONED_POSTS = new Set([
  "/api/set", "/api/patch", "/api/lock", "/api/spec", "/api/reset",
  "/api/prompt", "/api/build", "/api/optimise/preview", "/api/agent/variant",
  "/api/agent/undo", "/api/agent/redo", "/api/agent/snapshot", "/api/agent/restore",
  "/api/movements/propose",
  "/api/chat/approve", "/api/chat/reject", "/api/agent/approve",
  "/api/agent/reject", "/api/chat", "/api/agent/run",
]);

const esc = (value) => String(value)
  .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;");

/* --------------------------------------------------------------- markdown */
function mdInline(text) {
  let out = esc(text);
  out = out.replace(/`([^`]+)`/g, "<code>$1</code>");
  out = out.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  out = out.replace(/(^|[\s(])\*([^*\n]+)\*/g, "$1<em>$2</em>");
  out = out.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g,
    '<a href="$2" target="_blank" rel="noreferrer noopener">$1</a>');
  return out;
}

function mdRow(line) {
  return String(line).trim().replace(/^\|/, "").replace(/\|$/, "").split("|")
    .map((cell) => cell.trim());
}

/* A deliberately small, escaped Markdown renderer: headings, lists, tables,
   fenced code, quotes, bold/italic/inline-code and safe http(s) links. */
function renderMarkdown(text) {
  const lines = String(text ?? "").replaceAll("\r\n", "\n").split("\n");
  const out = [];
  let para = [];
  let i = 0;
  const flush = () => {
    if (!para.length) return;
    out.push(`<p>${para.map(mdInline).join("<br>")}</p>`);
    para = [];
  };
  while (i < lines.length) {
    const line = lines[i];
    if (/^\s*```/.test(line)) {
      flush();
      const lang = line.trim().replace(/^```/, "").trim();
      const code = [];
      i += 1;
      while (i < lines.length && !/^\s*```/.test(lines[i])) {
        code.push(lines[i]);
        i += 1;
      }
      i += 1;
      out.push(`<pre><code${lang ? ` data-lang="${esc(lang)}"` : ""}>${esc(code.join("\n"))}</code></pre>`);
      continue;
    }
    if (/^\s*$/.test(line)) { flush(); i += 1; continue; }
    const heading = line.match(/^(#{1,4})\s+(.*)$/);
    if (heading) {
      flush();
      const level = Math.min(6, heading[1].length + 2);
      out.push(`<h${level}>${mdInline(heading[2])}</h${level}>`);
      i += 1;
      continue;
    }
    if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) {
      flush(); out.push("<hr>"); i += 1; continue;
    }
    if (/^\s*>\s?/.test(line)) {
      flush();
      const quote = [];
      while (i < lines.length && /^\s*>\s?/.test(lines[i])) {
        quote.push(lines[i].replace(/^\s*>\s?/, ""));
        i += 1;
      }
      out.push(`<blockquote>${quote.map(mdInline).join("<br>")}</blockquote>`);
      continue;
    }
    if (/^\s*(?:\d+\.|[-*])\s+/.test(line)) {
      flush();
      const ordered = /^\s*\d+\./.test(line);
      const items = [];
      while (i < lines.length && /^\s*(?:\d+\.|[-*])\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*(?:\d+\.|[-*])\s+/, ""));
        i += 1;
      }
      const tag = ordered ? "ol" : "ul";
      out.push(`<${tag}>${items.map((item) => `<li>${mdInline(item)}</li>`).join("")}</${tag}>`);
      continue;
    }
    if (line.includes("|") && /^\s*\|?[\s:|-]+\|[\s:|-]*$/.test(lines[i + 1] || "")) {
      flush();
      const header = mdRow(line);
      i += 2;
      const rows = [];
      while (i < lines.length && lines[i].includes("|") && lines[i].trim()) {
        rows.push(mdRow(lines[i]));
        i += 1;
      }
      out.push(`<table><thead><tr>${header.map((cell) => `<th>${mdInline(cell)}</th>`).join("")}</tr></thead><tbody>${
        rows.map((row) => `<tr>${row.map((cell) => `<td>${mdInline(cell)}</td>`).join("")}</tr>`).join("")
      }</tbody></table>`);
      continue;
    }
    para.push(line);
    i += 1;
  }
  flush();
  return out.join("");
}

const fmt = (value, digits = 2) => {
  const n = Number(value);
  return Number.isFinite(n) ? n.toFixed(digits).replace(/\.?0+$/, "") : "—";
};

const slug = (text) => String(text || "watch").toLowerCase()
  .replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "watch";

function currentSession() {
  try {
    let id = window.localStorage?.getItem("watchcad.session");
    if (!id) {
      id = `web-${Math.random().toString(36).slice(2, 10)}`;
      window.localStorage?.setItem("watchcad.session", id);
    }
    return id;
  } catch {
    return "default";
  }
}

function rememberSession(id) {
  if (!id) return;
  const previous = AGENT.sessionId ? String(AGENT.sessionId) : "";
  const next = String(id);
  const changed = Boolean(previous && previous !== next);
  if (changed) {
    // A different design session has its own monotonic revision sequence.
    appliedRevision = 0;
    renderedRevision = 0;
    // A cloned session can have an identical design signature.  Force a scene
    // reload so the viewer sees the new session identity instead of treating
    // the unchanged checksum as "already rendered".
    sceneToken += 1;
    clearTimeout(sceneTimer);
    lastSceneSig = "";
    // A preview token belongs to the session that created it.
    DESIGN.previewToken += 1;
    DESIGN.previewId = "";
    for (const id of ["btnClearPreview", "btnClearPreviewToolbar"]) {
      const clear = $(`#${id}`);
      if (clear) clear.hidden = true;
    }
  }
  AGENT.sessionId = id;
  try {
    window.localStorage?.setItem("watchcad.session", next);
  } catch { /* private mode: keep the in-memory value */ }
  if (changed) {
    // Invalidate in-flight loads before the new session's scene event arrives.
    viewerEvent("watchcad:session-changed", { session: next, previous });
  }
}

function withSession(url) {
  const separator = String(url).includes("?") ? "&" : "?";
  return `${url}${separator}session=${encodeURIComponent(currentSession())}`;
}

async function api(path, body) {
  let payload = body;
  if (payload !== undefined && REVISIONED_POSTS.has(path)
      && payload.expected_revision === undefined) {
    const revision = appliedRevision || Number(STATE?.revision || 0);
    if (revision > 0) payload = { ...payload, expected_revision: revision };
  }
  const options = payload === undefined ? { headers: {} } : {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  };
  options.headers = options.headers || {};
  options.headers["X-WatchCAD-Session"] = currentSession();
  const response = await fetch(path, options);
  let data = {};
  try { data = await response.json(); } catch { /* empty response */ }
  try {
    Object.defineProperty(data, "__headers", {
      value: response.headers, enumerable: false, configurable: true,
    });
  } catch { /* non-extensible payload */ }
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}

function responseRevision(payload, name = "X-Design-Revision") {
  const raw = payload?.__headers?.get?.(name);
  const value = Number(raw);
  return Number.isFinite(value) ? value : 0;
}

function responseHeader(payload, name) {
  return String(payload?.__headers?.get?.(name) || "");
}

function livePreviewMeta() {
  return {
    session: currentSession(),
    revision: appliedRevision || Number(STATE?.revision || 0),
    checksum: String(STATE?.spec?.data?.derived?.checksum || ""),
    modification_id: String(STATE?.modification_id || ""),
    preview: false,
    preview_id: "",
  };
}

function revisionedUrl(path, revision) {
  if (!revision) return path;
  return `${path}${path.includes("?") ? "&" : "?"}revision=${revision}`;
}

function toast(message, bad = false) {
  const node = $("#toast");
  node.textContent = message;
  node.className = bad ? "on bad" : "on";
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { node.className = ""; }, 3200);
}

function setLoading(on, text = "solving geometry") {
  const node = $("#loading");
  if (!node) return;
  node.classList.toggle("on", Boolean(on));
  if (on && text) node.lastElementChild.innerHTML = `<div class="spinner"></div>${esc(text)}`;
}

function getPath(obj, path) {
  return path.split(".").reduce((node, key) =>
    (node && typeof node === "object" ? node[key] : undefined), obj);
}

/* ------------------------------------------------------------------ boot */
async function boot() {
  $("#btnParse").addEventListener("click", () => sendChat());
  $("#prompt").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      sendChat();
    }
  });
  $("#btnSave").addEventListener("click", saveSpec);
  $("#btnLoad").addEventListener("click", () => $("#fileLoad").click());
  $("#fileLoad").addEventListener("change", loadSpecFile);
  $("#btnNew").addEventListener("click", resetSpec);
  $("#btnBuildTop").addEventListener("click", buildPackage);
  $("#btnStop").addEventListener("click", stopRun);
  $("#btnUndo").addEventListener("click", undoTurn);
  $("#btnRedo").addEventListener("click", redoTurn);
  $("#btnHistory").addEventListener("click", openHistoryModal);
  $("#btnChats").addEventListener("click", toggleChatHistory);
  $("#btnNewChat").addEventListener("click", newChat);
  $("#btnSettings").addEventListener("click", toggleSettings);
  $("#btnProviders").addEventListener("click", openProviders);
  $("#btnProviderSettings").addEventListener("click", openProviders);
  $("#btnProviderTest").addEventListener("click", testProvider);
  $("#btnProviderSave").addEventListener("click", saveProvider);
  $("#btnProviderRemove").addEventListener("click", removeProvider);
  $("#providerModal").addEventListener("click", (event) => {
    if (event.target === event.currentTarget) closeProviders();
  });
  for (const id of ["movementModal", "historyModal", "compareModal"]) {
    $(`#${id}`)?.addEventListener?.("click", (event) => {
      if (event.target === event.currentTarget) hideModal(id);
    });
  }
  $("#btnVariations").addEventListener("click", generateVariations);
  $("#btnDesignPrompt").addEventListener("click", describeFromReference);
  $("#btnDesignRef").addEventListener("click", () => $("#designReference").click());
  $("#designReference").addEventListener("change", (event) => {
    attachImages(event);
    renderDesignReferences();
  });
  $("#designPanel").addEventListener("change", onDesignChange);
  $("#btnOptimise").addEventListener("click", runOptimisation);
  ["optimiseAxisX", "optimiseAxisY"].forEach((id) => {
    $(`#${id}`)?.addEventListener("change", () => {
      const x = $("#optimiseAxisX")?.value;
      const y = $("#optimiseAxisY")?.value;
      if (!x || !y || x === y) return;
      DESIGN.optimiseAxes = [x, y];
      if (DESIGN.optimisation) runOptimisation();
    });
  });
  $("#btnClearPreview")?.addEventListener("click", clearCandidatePreview);
  $("#btnClearPreviewToolbar")?.addEventListener("click", clearCandidatePreview);
  $("#btnExpandTree")?.addEventListener("click", () => setTreeExpanded(true));
  $("#btnCollapseTree")?.addEventListener("click", () => setTreeExpanded(false));
  $("#componentSearch")?.addEventListener("input", (event) => {
    TREE.query = String(event.target.value || "").trim().toLowerCase();
    renderComponentTree();
  });
  $("#btnSnapshot")?.addEventListener("click", createSnapshot);
  $("#btnRefreshVersions")?.addEventListener("click", loadVersions);
  $("#btnMovementSearch").addEventListener("click", loadMovementList);
  $("#movementSearch").addEventListener("keydown", (event) => {
    if (event.key === "Enter") { event.preventDefault(); loadMovementList(); }
  });
  $("#movementMaker").addEventListener("change", loadMovementList);
  $("#movementType").addEventListener("change", loadMovementList);
  $("#movementMaxThickness")?.addEventListener("change", loadMovementList);
  $("#movementUnknown").addEventListener("change", loadMovementList);
  $("#btnAttach").addEventListener("click", () => $("#chatImages").click());
  $("#chatImages").addEventListener("change", attachImages);
  $("#conversationSearch").addEventListener("input", (event) => {
    loadConversations(event.target.value || "");
  });
  $("#settingsBox").addEventListener("click", (event) => {
    if (event.target.closest("[data-settings-save]")) saveSettings();
  });
  $("#autonomy").addEventListener("change", (event) => {
    try { window.localStorage?.setItem("watchcad.autonomy", event.target.value); } catch { /* ignore */ }
    toast(`mode: ${event.target.value}`);
  });
  $("#examples").addEventListener("click", (event) => {
    const button = event.target.closest("[data-preset]");
    if (!button) return;
    const prompt = IDEAS[button.dataset.preset];
    if (prompt) sendChat(prompt);
  });
  $("#specForm").addEventListener("change", onFieldChange);
  $("#specForm").addEventListener("click", onFieldStep);
  $("#explode").addEventListener("input", (event) => {
    setExplodeValue(Number(event.target.value), true);
  });
  const quality = $("#renderQuality");
  if (quality) {
    try { quality.value = window.localStorage?.getItem("watchcad.renderQuality") || "high"; }
    catch { quality.value = "high"; }
    quality.addEventListener("change", (event) => {
      viewerEvent("watchcad:quality", { name: event.target.value });
      if (!window.WatchCAD3D?.available) {
        renderGroupToggles(V.tree);
        requestRender();
      }
      toast(`render: ${event.target.value}`);
    });
  }
  const background = $("#renderBackground");
  if (background) {
    try { background.value = window.localStorage?.getItem("watchcad.background") || "studio"; }
    catch { background.value = "studio"; }
    background.addEventListener("change", (event) => {
      viewerEvent("watchcad:background", { name: event.target.value });
    });
  }
  document.addEventListener("click", onGlobalClick);
  document.addEventListener("keydown", onGlobalKeydown);
  setupViewer();
  restorePanels();
  restoreDock();
  bindViewerBridge();
  try {
    setWorkspace(window.localStorage?.getItem("watchcad.workspace") || "design");
  } catch {
    setWorkspace("design");
  }
  try {
    const savedMode = window.localStorage?.getItem("watchcad.autonomy");
    if (AGENT_MODES.includes(savedMode)) $("#autonomy").value = savedMode;
  } catch { /* private mode or a non-browser harness */ }

  try {
    const [schema, state, components] = await Promise.all([
      api("/api/schema"), api("/api/state"),
      api("/api/components").catch(() => ({ components: [] })),
    ]);
    SCHEMA = schema;
    STATE = state;
    // /api/state identifies the session that produced this snapshot.  Record it
    // before any edits so later conversation switches are recognised as real
    // session changes and can invalidate in-flight scene loads.
    rememberSession(state.session?.session_id || currentSession());
    appliedRevision = Number(state.revision || 0) || appliedRevision;
    IDEAS = Object.fromEntries((schema.ideas || []).map((item) => [item.id, item.prompt]));
    setComponents(components);
    renderExamples();
    await initChat();
    renderAll();
    ensureViewerSize();
    await loadScene(true);
    setViewFromHash();
  } catch (error) {
    toast(`cannot reach the engine: ${error.message}`, true);
  }
}

function renderAll() {
  renderHeader();
  renderFacts();
  renderSpec();
  renderChecks();
  renderPackage();
  renderChat();
  renderAgent();
  renderDesignPanel();
  renderOptimisation();
  renderCritic();
  renderMovement();
  renderComponentTree();
  renderVerificationDock();
  renderTimelineDock();
  renderComparisonDock();
}

function applyState(state, options = {}) {
  if (!state || !state.spec) return false;
  if (state.session?.session_id) rememberSession(state.session.session_id);
  const revision = Number(state.revision || responseRevision(state) || 0);
  if (revision && revision < appliedRevision) return false;
  const previous = STATE && STATE.spec && STATE.spec.data.derived
    ? STATE.spec.data.derived.checksum : "";
  const previousRevision = appliedRevision;
  STATE = state;
  if (revision) appliedRevision = revision;
  const checksum = state.spec?.data?.derived?.checksum || "";
  renderHeader();
  renderFacts();
  renderChecks();
  renderPackage();
  if (options.spec === false) syncFormValues();
  else renderSpec();
  renderDesignPanel();
  renderOptimisation();
  renderCritic();
  renderMovement();
  renderComponentTree();
  renderVerificationDock();
  if (options.scene !== false
      && (checksum !== previous || revision !== previousRevision)) scheduleScene();
  renderSessionChip();
  if (state.render) {
    viewerEvent("watchcad:render-status", state.render);
    if (state.render.status === "failed" && state.render.error) {
      toast(`render failed: ${String(state.render.error).slice(0, 140)}`, true);
    }
  }
  return true;
}

/* ---------------------------------------------------------- workspaces */
const WORKSPACE_COPY = {
  design: {
    label: "Design Studio",
    hint: "Shape the design; every edit is solved and rebuilt.",
  },
  engineering: {
    label: "Engineering Studio",
    hint: "Inspect the canonical mesh, constraints and movement fit.",
  },
  optimise: {
    label: "Optimisation Lab",
    hint: "Compare deterministic candidates; nothing applies without confirmation.",
  },
};

function setWorkspace(name) {
  const next = ["design", "engineering", "optimise"].includes(name) ? name : "design";
  if (document.body?.dataset) document.body.dataset.workspace = next;
  $$("#workspaces button").forEach((button) =>
    button.classList.toggle("active", button.dataset.workspace === next));
  const copy = WORKSPACE_COPY[next];
  const label = $("#workspaceLabel");
  const hint = $("#workspaceHint");
  if (label) label.textContent = copy.label;
  if (hint) hint.textContent = copy.hint;
  try { window.localStorage?.setItem("watchcad.workspace", next); } catch { /* ignore */ }
  if (next === "design") renderDesignPanel();
  if (next === "optimise") { renderOptimisation(); renderComparisonDock(); }
  if (next === "engineering") renderVerificationDock();
  ensureViewerSize();
  requestRender();
}

/* --------------------------------------------------------- design studio */
function designField(path) {
  const [group, name] = String(path).split(".");
  return (SCHEMA?.groups?.[group] || []).find((field) => field.name === name);
}

function fillDesignSelect(select, path, value) {
  if (!select) return;
  const field = designField(path);
  const choices = field?.choices || [];
  if (!choices.length) return;
  const markup = choices.map((choice) =>
    `<option value="${esc(choice)}">${esc(choice)}</option>`).join("");
  if (select.innerHTML !== markup) select.innerHTML = markup;
  const current = String(value ?? "");
  if (choices.includes(current)) select.value = current;
  else if (!select.value) select.value = choices[0];
}

function renderDesignPanel() {
  if (!STATE || !SCHEMA) return;
  const spec = STATE.spec.data;
  for (const select of $$("#designPanel [data-design-path]")) {
    fillDesignSelect(select, select.dataset.designPath, getPath(spec, select.dataset.designPath));
  }
  renderDesignSwatches();
  renderDesignReferences();
  renderDesignVariations();
}

function renderDesignSwatches() {
  const holder = $("#designSwatches");
  if (!holder) return;
  const current = String(STATE?.spec?.data?.dial?.color || "").toLowerCase();
  holder.innerHTML = DIAL_SWATCHES.map(([name, hex]) =>
    `<button type="button" data-dial-color="${esc(name)}" title="${esc(name)}"
      class="${current === name ? "active" : ""}" style="background:${esc(hex)}"></button>`).join("");
  const input = $("#designDialColor");
  if (!input) return;
  const swatch = DIAL_SWATCHES.find(([name]) => name === current);
  if (swatch) input.value = swatch[1];
  else if (/^#[0-9a-f]{6}$/i.test(current)) input.value = current;
}

function renderDesignReferences() {
  const strip = $("#designRefStrip");
  if (!strip) return;
  const images = CHAT.attachments || [];
  strip.hidden = !images.length;
  strip.innerHTML = images.map((url, index) => `<figure class="attachment">
    <img src="${esc(url)}" alt="Reference image ${index + 1}">
    <button data-remove-attachment="${index}" title="Remove reference">×</button>
  </figure>`).join("");
}

function renderDesignVariations() {
  const holder = $("#designVariations");
  if (!holder) return;
  const variants = AGENT.variants || [];
  holder.innerHTML = variants.map((variant, index) => {
    const after = variant.verification || {};
    const palette = (variant.visual?.palette || []).map((hex) =>
      `<i style="background:${esc(hex)}"></i>`).join("");
    return `<div class="variant">
      <div class="variant-head"><b>${esc(variant.label || `Alternative ${index + 1}`)}</b>
        <button class="btn small" data-variant="${index}">Apply</button></div>
      <p>${esc(variant.rationale || "")}</p>
      ${palette ? `<div class="swatch-line">${palette}</div>` : ""}
      <div class="variant-meta">${Number(after.fail || 0)} failing · checksum ${esc(String(variant.checksum || "").slice(0, 8))}</div>
    </div>`;
  }).join("");
}

function onDesignChange(event) {
  const target = event.target;
  if (!target) return;
  if (target.id === "designDialColor") {
    setField("dial.color", target.value);
    return;
  }
  const select = target.closest?.("[data-design-path]");
  if (select) setField(select.dataset.designPath, select.value);
}

function describeFromReference() {
  if (!CHAT.attachments.length) {
    toast("attach a reference image first", true);
    $("#designReference").click();
    return;
  }
  sendChat([
    "Use the attached reference image as visual guidance for this design.",
    "Keep every dimension and feasibility claim owned by the solver, and list",
    "which visible details (case shape, dial layout, colours, indices, hands)",
    "you actually used. If the configured model cannot see images, say so",
    "instead of guessing.",
  ].join(" "));
}

async function generateVariations() {
  if (!STATE) return;
  setLoading(true, "generating deterministic alternatives");
  try {
    const data = await api("/api/agent/variants");
    AGENT.variants = data.variants || [];
    renderDesignVariations();
    renderAgentVariants();
    toast(AGENT.variants.length
      ? `${AGENT.variants.length} alternatives ready; review before applying`
      : "no alternative changes were available for this design");
  } catch (error) {
    toast(error.message, true);
  } finally {
    setLoading(false);
  }
}

/* ------------------------------------------------------ optimisation lab */
function metric(value, digits = 2) {
  if (value === null || value === undefined || value === "") return "—";
  return typeof value === "number" ? fmt(value, digits) : String(value);
}

function optMeta(name, result) {
  return (result?.objectives || {})[name] || {
    label: name, unit: "", direction: "min", provenance: "calculated",
  };
}

function optimisationStale(result) {
  const current = STATE?.spec?.data?.derived?.checksum || "";
  return Boolean(result?.baseline_checksum && current
    && result.baseline_checksum !== current);
}

function populateAxes(result) {
  const axes = result?.axes || DESIGN.optimiseAxes || ["weight", "cost"];
  for (const id of ["optimiseAxisX", "optimiseAxisY"]) {
    const select = $(`#${id}`);
    if (!select) continue;
    const names = Object.keys(result?.objectives || {}).length
      ? Object.keys(result.objectives) : ["weight", "cost"];
    const markup = names.map((name) => {
      const meta = optMeta(name, result);
      return `<option value="${esc(name)}">${esc(meta.label || name)}</option>`;
    }).join("");
    if (select.innerHTML !== markup) select.innerHTML = markup;
    const desired = axes[id === "optimiseAxisX" ? 0 : 1]
      || (id === "optimiseAxisX" ? "weight" : "cost");
    select.value = desired === select.value ? desired : (names.includes(desired) ? desired : names[0]);
  }
}

function renderScatter(result, candidates) {
  const svg = $("#optimiseScatter");
  const legend = $("#optimiseLegend");
  if (!svg || !result) return;
  const [xName, yName] = result.axes || DESIGN.optimiseAxes || [];
  if (!xName || !yName) { svg.innerHTML = ""; return; }
  const xMeta = optMeta(xName, result);
  const yMeta = optMeta(yName, result);
  const usable = candidates.filter((item) => Number.isFinite(
    Number(item.objective_values?.[xName])) && Number.isFinite(
    Number(item.objective_values?.[yName])));
  const xValues = usable.map((item) => Number(item.objective_values[xName]));
  const yValues = usable.map((item) => Number(item.objective_values[yName]));
  let xMin = Math.min(...xValues, 0);
  let xMax = Math.max(...xValues, 1);
  let yMin = Math.min(...yValues, 0);
  let yMax = Math.max(...yValues, 1);
  if (xMax - xMin < 1e-9) xMax += 1;
  if (yMax - yMin < 1e-9) yMax += 1;
  const px = (value) => 46 + (Number(value) - xMin) / (xMax - xMin) * 286;
  const py = (value) => 204 - (Number(value) - yMin) / (yMax - yMin) * 156;
  const front = new Set(result.frontier || []);

  const grid = [0, 0.5, 1].map((part) => {
    const gx = 46 + part * 286;
    const gy = 204 - part * 156;
    return `<line x1="${gx}" y1="48" x2="${gx}" y2="204" class="opt-grid"/>
      <line x1="46" y1="${gy}" x2="332" y2="${gy}" class="opt-grid"/>
      <text x="${gx}" y="222" text-anchor="middle" class="opt-tick">${metric(xMin + part * (xMax - xMin), 1)}</text>
      <text x="38" y="${gy + 3}" text-anchor="end" class="opt-tick">${metric(yMin + part * (yMax - yMin), 1)}</text>`;
  }).join("");

  const points = usable.map((item) => {
    const x = px(item.objective_values[xName]);
    const y = py(item.objective_values[yName]);
    const cls = [item.id === "baseline" ? "baseline" : "",
      item.feasible ? (front.has(item.id) ? "frontier" : "dominated") : "infeasible",
      DESIGN.selectedCandidate === item.id ? "selected" : ""].filter(Boolean).join(" ");
    const title = `${item.label}: ${xName} ${metric(item.objective_values[xName])}, ${yName} ${metric(item.objective_values[yName])}`;
    return `<g class="opt-point ${cls}" data-select-candidate="${esc(item.id)}" role="button" tabindex="0">
      <title>${esc(title)}</title>
      <circle cx="${x}" cy="${y}" r="${front.has(item.id) ? 7 : 5}"/>
      ${DESIGN.selectedCandidate === item.id
        ? `<circle class="opt-ring" cx="${x}" cy="${y}" r="11"/>` : ""}
    </g>`;
  }).join("");

  svg.innerHTML = `${grid}
    <line x1="46" y1="204" x2="332" y2="204" class="opt-axis"/>
    <line x1="46" y1="48" x2="46" y2="204" class="opt-axis"/>
    <text x="189" y="244" text-anchor="middle" class="opt-label">${esc(xMeta.label || xName)} (${esc(xMeta.direction === "max" ? "higher better" : "lower better")})</text>
    <text x="12" y="126" text-anchor="middle" transform="rotate(-90 12 126)" class="opt-label">${esc(yMeta.label || yName)} (${esc(yMeta.direction === "max" ? "higher better" : "lower better")})</text>
    ${points}`;
  if (legend) {
    legend.innerHTML = `<span class="opt-key frontier">Pareto frontier</span>
      <span class="opt-key dominated">dominated</span>
      <span class="opt-key baseline">baseline</span>
      <span class="opt-key infeasible">constraint failure</span>`;
  }
}

function renderCandidateDetail(candidate, result) {
  const holder = $("#candidateDetail");
  if (!holder) return;
  if (!candidate) {
    holder.innerHTML = `<div class="empty-note">Select a point or a candidate card to inspect its diff, constraints and verification report.</div>`;
    return;
  }
  const stale = optimisationStale(result);
  const base = result.baseline || {};
  const objectives = (result.axes || []).map((name) => {
    const meta = optMeta(name, result);
    const value = candidate.objective_values?.[name];
    const before = base.objective_values?.[name];
    const delta = Number.isFinite(Number(value)) && Number.isFinite(Number(before))
      ? Number(value) - Number(before) : null;
    return `<tr><td>${esc(meta.label || name)}</td>
      <td>${metric(before)}</td><td><b>${metric(value)}</b></td>
      <td>${delta === null ? "—" : `${delta >= 0 ? "+" : ""}${metric(delta, 3)}`}</td>
      <td>${esc(meta.provenance || "calculated")}</td></tr>`;
  }).join("");
  const diff = (candidate.applied || []).map((change) => `<tr>
      <td><code>${esc(change.path)}</code></td>
      <td>${esc(metric(change.from))}</td><td>${esc(metric(change.to))}</td></tr>`).join("")
    || `<tr><td colspan="3">No field changes.</td></tr>`;
  const constraints = (candidate.constraints || []).map((row) => `<tr>
      <td><span class="fit-badge ${esc(row.status)}">${esc(row.status)}</span></td>
      <td>${esc(row.id)}</td><td>${esc(row.detail)}</td>
      <td>${esc(row.provenance || "")}</td></tr>`).join("");
  const armed = DESIGN.pendingApply === candidate.id;
  const canApply = !stale && candidate.feasible && candidate.status === "ok"
    && Boolean(candidate.changes?.length);
  holder.innerHTML = `<div class="candidate-detail-head">
      <h3>${esc(candidate.label || candidate.id)}</h3>
      <span class="provenance ${candidate.feasible ? "calculated" : "failed"}">
        ${candidate.feasible ? "constraints pass" : "constraint failure"}</span>
    </div>
    <p>${esc(candidate.rationale || "")}</p>
    <div class="candidate-detail-grid">
      <div><h4>Objectives vs baseline</h4>
        <table class="mini-table"><thead><tr><th>Axis</th><th>Baseline</th><th>Candidate</th><th>Δ</th><th>Source</th></tr></thead>
        <tbody>${objectives || `<tr><td colspan="5">No axes selected.</td></tr>`}</tbody></table>
      </div>
      <div><h4>Proposed diff</h4>
        <table class="mini-table"><thead><tr><th>Field</th><th>From</th><th>To</th></tr></thead>
        <tbody>${diff}</tbody></table>
      </div>
    </div>
    <h4>Hard constraints</h4>
    <table class="mini-table"><thead><tr><th>Status</th><th>Constraint</th><th>Detail</th><th>Source</th></tr></thead>
      <tbody>${constraints}</tbody></table>
    <p class="provenance">verification: ${metric(candidate.verification?.fail, 0)} failing ·
      ${metric(candidate.verification?.needs_test, 0)} need bench testing ·
      ${metric(candidate.metrics?.stack_margin_mm)} mm stack margin</p>
    <div class="candidate-foot">
      <button class="btn small" data-preview-candidate="${esc(candidate.id)}"
        ${candidate.status !== "ok" ? "disabled" : ""}>Preview in 3D</button>
      <button class="btn small" data-apply-candidate="${esc(candidate.id)}"
        ${canApply ? "" : "disabled"}>${armed ? "Change selection" : "Apply…"}</button>
      ${armed ? `<button class="btn small primary" data-confirm-candidate="${esc(candidate.id)}">Confirm apply</button>
        <button class="btn small" data-cancel-apply="1">Cancel</button>` : ""}
      <button class="btn small" data-undo-candidate="1">Undo last apply</button>
    </div>
    <p class="design-note">Applying writes a new checked version through the validated variant pipeline. Undo restores the previous version; no candidate is applied without confirmation.</p>`;
}

function renderOptimisation() {
  const holder = $("#candidateList");
  const status = $("#optimiseStatus");
  if (!holder || !status) return;
  const result = DESIGN.optimisation;
  const current = STATE?.spec?.data?.derived?.checksum || "";
  if (!result) {
    holder.innerHTML = "";
    renderScatter(null, []);
    renderCandidateDetail(null, null);
    if (!DESIGN.optimiseBusy) {
      status.textContent = "Not run yet. The current design is the baseline.";
    }
    return;
  }
  populateAxes(result);
  const stale = optimisationStale(result);
  const candidates = result.candidates || [];
  const winner = candidates.find((item) => item.id === result.winner);
  const frontier = new Set(result.frontier || []);
  status.innerHTML = `${stale
    ? "<b>Design changed since this run.</b> Rerun to compare against the current spec. "
    : ""}${candidates.length} candidates · ${metric(result.feasible_count, 0)} feasible ·
    objective <b>${esc(result.objective)}</b> · recommendation <b>${esc(winner?.label || "none")}</b>
    · Pareto front <b>${metric(frontier.size, 0)}</b>
    <br><span class="provenance">seed ${esc(result.reproducibility?.seed || "—")} ·
    finite deterministic probe set · no global-optimality claim · no physical verification</span>`;
  holder.innerHTML = candidates.map((candidate) => {
    const m = candidate.metrics || {};
    const isWinner = candidate.id === result.winner;
    const isFront = frontier.has(candidate.id);
    const disabled = stale || !candidate.feasible || !candidate.changes?.length
      || candidate.status !== "ok";
    return `<article class="candidate ${isWinner ? "winner" : ""} ${isFront ? "frontier" : ""}
        ${candidate.feasible ? "" : "failed"} ${DESIGN.selectedCandidate === candidate.id ? "selected" : ""}"
        data-inspect-candidate="${esc(candidate.id)}">
      <div class="candidate-head">
        <b>${esc(candidate.label || candidate.id)}</b>
        <span class="candidate-rank">#${esc(candidate.rank ?? "—")}${isWinner ? " · best" : ""}${isFront ? " · frontier" : ""}</span>
      </div>
      <p>${esc(candidate.rationale || "")}</p>
      <div class="candidate-metrics">
        <span>mass <b>${metric(m.mass_g)} g</b></span>
        <span>unit cost <b>$${metric(m.estimated_unit_cost_usd)}</b></span>
        <span>material <b>$${metric(m.material_cost_usd)}</b></span>
        <span>parts <b>${metric(m.part_count, 0)}</b></span>
        <span>DFM fails <b>${metric(m.dfm_failures, 0)}</b></span>
        <span>stack margin <b>${metric(m.stack_margin_mm)} mm</b></span>
        <span>checks fail <b>${metric(m.failing_checks, 0)}</b></span>
        <span>needs test <b>${metric(m.needs_test, 0)}</b></span>
      </div>
      <div class="candidate-foot">
        <span class="provenance ${candidate.feasible ? (candidate.measurable ? "calculated" : "estimated") : "failed"}">${candidate.feasible
          ? (candidate.measurable ? "change measured · constraints pass" : "visual change only")
          : "constraint failure"}</span>
        <button class="btn small" data-apply-candidate="${esc(candidate.id)}"
          ${disabled ? "disabled" : ""} title="${stale ? "Rerun after the design changed" : "Review and confirm before applying"}">Apply…</button>
      </div>
      ${candidate.error ? `<p class="provenance failed">${esc(candidate.error)}</p>` : ""}
    </article>`;
  }).join("");
  renderScatter(result, candidates);
  const selected = candidates.find((item) => item.id === DESIGN.selectedCandidate);
  renderCandidateDetail(selected, result);
  renderCandidateCompare();
  renderComparisonDock();
}

async function runOptimisation() {
  if (DESIGN.optimiseBusy) return;
  const objective = $("#optimiseObjective")?.value || "weight";
  const budget = Number($("#optimiseBudget")?.value || 9);
  const x = $("#optimiseAxisX")?.value || DESIGN.optimiseAxes[0] || "weight";
  const y = $("#optimiseAxisY")?.value || DESIGN.optimiseAxes[1] || "cost";
  if (x === y) { toast("scatter axes must be different", true); return; }
  DESIGN.optimiseAxes = [x, y];
  const token = ++DESIGN.optimiseToken;
  DESIGN.optimiseBusy = true;
  DESIGN.pendingApply = null;
  const status = $("#optimiseStatus");
  if (status) status.textContent = `Solving and verifying ${budget} candidates…`;
  setLoading(true, "solving optimisation candidates");
  try {
    const result = await api("/api/optimise", { objective, budget, axes: [x, y] });
    if (token !== DESIGN.optimiseToken) return;
    DESIGN.optimisation = result;
    if (!result.candidates?.some((item) => item.id === DESIGN.selectedCandidate)) {
      DESIGN.selectedCandidate = null;
    }
    renderOptimisation();
    const winner = (result.candidates || []).find((item) => item.id === result.winner);
    toast(winner ? `recommendation: ${winner.label}` : "optimisation returned no candidates");
  } catch (error) {
    if (status) status.textContent = `Optimisation failed: ${error.message}`;
    toast(error.message, true);
  } finally {
    DESIGN.optimiseBusy = false;
    setLoading(false);
  }
}

async function previewCandidate(id) {
  const result = DESIGN.optimisation;
  const candidate = (result?.candidates || []).find((item) => item.id === id);
  if (!candidate) { toast("that candidate is no longer available", true); return; }
  if (optimisationStale(result)) {
    toast("rerun optimisation after the design changed", true);
    return;
  }
  const token = ++DESIGN.previewToken;
  setLoading(true, `building 3D preview of ${candidate.label}`);
  try {
    const payload = await api("/api/optimise/preview", {
      changes: candidate.changes || [],
      baseline_checksum: result.baseline_checksum,
      label: candidate.label,
    });
    if (token !== DESIGN.previewToken) return;
    const received = Number(payload.revision || responseRevision(payload) || 0);
    if (received && received !== appliedRevision) {
      toast("the design changed; preview discarded", true);
      return;
    }
    DESIGN.previewId = payload.preview;
    for (const id of ["btnClearPreview", "btnClearPreviewToolbar"]) {
      const button = $(`#${id}`);
      if (button) button.hidden = false;
    }
    viewerEvent("watchcad:model-url", {
      url: `/api/model.glb?preview=${encodeURIComponent(payload.preview)}`,
      session: currentSession(),
      revision: received,
      checksum: String(payload.checksum || ""),
      modification_id: responseHeader(payload, "X-Modification-ID")
        || String(STATE?.modification_id || ""),
      preview: true,
      preview_id: String(payload.preview || ""),
    });
    toast(`previewing ${candidate.label}; the live design is unchanged`);
  } catch (error) {
    toast(error.message, true);
  } finally {
    setLoading(false);
  }
}

function clearCandidatePreview() {
  DESIGN.previewToken += 1;
  DESIGN.previewId = "";
  for (const id of ["btnClearPreview", "btnClearPreviewToolbar"]) {
    const button = $(`#${id}`);
    if (button) button.hidden = true;
  }
  viewerEvent("watchcad:model-url", { url: "", ...livePreviewMeta() });
}

function armCandidateApply(id) {
  DESIGN.pendingApply = id;
  DESIGN.selectedCandidate = id;
  renderOptimisation();
  toast("review the diff, then confirm the apply");
}

async function applyCandidate(id) {
  const result = DESIGN.optimisation;
  const candidate = (result?.candidates || []).find((item) => item.id === id);
  if (!candidate) { toast("that candidate is no longer available", true); return; }
  const current = STATE?.spec?.data?.derived?.checksum || "";
  if (result.baseline_checksum && current && result.baseline_checksum !== current) {
    toast("the design changed; rerun optimisation before applying", true);
    return;
  }
  if (!candidate.feasible) { toast("candidate failed a hard constraint", true); return; }
  const changes = (candidate.changes || []).map((change) => ({
    path: change.path, to: change.value, from: change.from,
  }));
  if (!changes.length) { toast("this candidate has no changes", true); return; }
  setLoading(true, `applying ${candidate.label}`);
  try {
    const payload = await api("/api/agent/variant", {
      variant: { label: candidate.label, rationale: candidate.rationale, changes },
      mode: "safe",
    });
    applyAgentResult(payload);
    CHAT.messages.push(assistantFromAgent(payload));
    renderChat();
    DESIGN.pendingApply = null;
    DESIGN.selectedCandidate = id;
    clearCandidatePreview();
    renderOptimisation();
    const failed = Number(payload.verification?.fail ?? payload.summary?.fail ?? 0);
    toast(failed
      ? `applied with ${failed} failing check(s); use Undo to roll back`
      : `applied ${candidate.label} as a new version; Undo is available`);
  } catch (error) {
    toast(error.message, true);
  } finally {
    setLoading(false);
  }
}

/* ------------------------------------------------------- studio dialogs */
function hideModal(id) {
  const modal = $(`#${id}`);
  if (modal) modal.hidden = true;
}

function showModal(id) {
  const modal = $(`#${id}`);
  if (modal) modal.hidden = false;
}

function openMovementModal() {
  showModal("movementModal");
  if (!MOVEMENT.catalog) loadMovementCatalog();
  else renderMovement();
}

async function openHistoryModal() {
  showModal("historyModal");
  await loadVersions();
}

function openCompareModal() {
  showModal("compareModal");
  if (!WORKSPACE.compare.size && DESIGN.optimisation?.winner) {
    WORKSPACE.compare.add(DESIGN.optimisation.winner);
  }
  renderCandidateCompare();
}

async function loadVersions() {
  try {
    const data = await api("/api/agent/history");
    WORKSPACE.versions = data.history || [];
    if (STATE?.session?.versions) STATE.session.versions = WORKSPACE.versions;
    renderVersionList();
    renderTimelineDock();
    if (WORKSPACE.historyVersion) await selectVersion(WORKSPACE.historyVersion);
    return WORKSPACE.versions;
  } catch (error) {
    const list = $("#versionList");
    if (list) list.innerHTML = `<div class="empty-note">History unavailable: ${esc(error.message)}</div>`;
    return [];
  }
}

function renderVersionList() {
  const holder = $("#versionList");
  if (!holder) return;
  if (!WORKSPACE.versions.length) {
    holder.innerHTML = `<div class="empty-note">No versions yet. Create a snapshot to capture this design.</div>`;
    return;
  }
  holder.innerHTML = WORKSPACE.versions.slice().reverse().map((version) =>
    `<div class="version-row ${WORKSPACE.historyVersion === version.id ? "selected" : ""}">
      <span class="version-copy"><b>${esc(version.label || `Version ${version.index ?? ""}`)}</b>
        <small>${esc(version.kind || "change")} · ${esc(formatVersionTime(version.at))} · ${esc(String(version.checksum || "").slice(0, 12))}</small></span>
      <span class="version-actions">
        <button class="btn small" data-version-compare="${esc(version.id)}">Compare</button>
        <button class="btn small" data-version-restore="${esc(version.id)}">Restore</button>
      </span>
    </div>`).join("");
  renderVersionCompare();
}

async function selectVersion(id) {
  WORKSPACE.historyVersion = id;
  renderVersionList();
  const holder = $("#versionCompare");
  if (!holder) return;
  holder.innerHTML = `<div class="empty-note">Loading version…</div>`;
  try {
    const version = await api(`/api/agent/version?id=${encodeURIComponent(id)}`);
    renderVersionDiff(version);
  } catch (error) {
    holder.innerHTML = `<div class="empty-note">Cannot load version: ${esc(error.message)}</div>`;
  }
}

function flattenSpec(value, prefix = "", out = {}) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    out[prefix] = value;
    return out;
  }
  for (const [key, child] of Object.entries(value)) {
    if (key === "derived") continue;
    const path = prefix ? `${prefix}.${key}` : key;
    if (child && typeof child === "object" && !Array.isArray(child)) flattenSpec(child, path, out);
    else out[path] = child;
  }
  return out;
}

function renderVersionDiff(version) {
  const holder = $("#versionCompare");
  if (!holder) return;
  const before = flattenSpec(version.spec || {});
  const after = flattenSpec(STATE?.spec?.data || {});
  const paths = [...new Set([...Object.keys(before), ...Object.keys(after)])].filter((path) =>
    JSON.stringify(before[path]) !== JSON.stringify(after[path])).slice(0, 60);
  const rows = paths.map((path) => `<div class="diff-line">
      <span>${esc(path)}</span><span class="from">${esc(metric(before[path]))}</span>
      <span class="to">${esc(metric(after[path]))}</span></div>`).join("")
    || `<div class="empty-note">This version matches the current writable specification.</div>`;
  holder.innerHTML = `<div class="design-head"><h3>${esc(version.label || `Version ${version.index}`)}</h3>
      <span class="provenance">${esc(version.kind || "change")}</span></div>
    <p class="design-note">Left: saved version · middle: version value · right: current design. Comparing does not change the live specification.</p>
    ${rows}`;
}

function armVersionRestore(id) {
  const version = WORKSPACE.versions.find((item) => item.id === id);
  if (!version) return;
  WORKSPACE.restoreId = id;
  const holder = $("#restoreConfirm");
  if (!holder) return;
  holder.hidden = false;
  holder.innerHTML = `<b>Restore “${esc(version.label || `Version ${version.index}`)}”?</b>
    This commits the saved design as a new revision and increments the version counter. The current design remains in history.
    <div class="modal-actions">
      <button class="btn small primary" data-confirm-restore="${esc(id)}">Confirm restore</button>
      <button class="btn small ghost" data-cancel-restore="1">Cancel</button>
    </div>`;
}

async function createSnapshot() {
  const label = $("#snapshotLabel")?.value?.trim() || "Manual snapshot";
  try {
    const payload = await api("/api/agent/snapshot", { label });
    applyState(payload, { scene: false });
    await loadVersions();
    toast(`snapshot created: ${label}`);
  } catch (error) {
    toast(error.message, true);
  }
}

async function restoreVersion(id) {
  try {
    const payload = await api("/api/agent/restore", { id });
    if (!applyState(payload)) {
      toast("the design changed while restoring; refresh history and try again", true);
      return;
    }
    WORKSPACE.restoreId = null;
    const confirm = $("#restoreConfirm");
    if (confirm) confirm.hidden = true;
    await loadVersions();
    toast("version restored as a new revision; Undo is available");
  } catch (error) {
    toast(error.message, true);
  }
}

function renderCandidateCompare() {
  const holder = $("#candidateCompare");
  if (!holder) return;
  const result = DESIGN.optimisation;
  if (!result) {
    holder.innerHTML = `<div class="empty-note">Run the optimiser first.</div>`;
    return;
  }
  const candidates = result.candidates || [];
  const selected = [...WORKSPACE.compare].filter((id) =>
    id === "baseline" || candidates.some((item) => item.id === id));
  const chosen = comparisonRows();
  const axes = result.axes || [];
  const chooser = [`<label class="movement-check"><input type="checkbox" data-compare-toggle="baseline"
    ${selected.includes("baseline") ? "checked" : ""}> baseline</label>`,
  ...candidates.map((item) => `<label class="movement-check"><input type="checkbox"
    data-compare-toggle="${esc(item.id)}" ${selected.includes(item.id) ? "checked" : ""}> ${esc(item.label || item.id)}</label>`)].join("");
  const table = chosen.length ? `<table class="compare-table">
      <thead><tr><th>Design</th>${axes.map((name) =>
        `<th>${esc(optMeta(name, result).label || name)}</th>`).join("")}<th>Mass</th><th>Cost</th><th>Verdict</th><th>Action</th></tr></thead>
      <tbody>${chosen.map((row) => `<tr>
        <td><b>${esc(row.label || "Baseline")}</b></td>
        ${axes.map((name) => `<td>${metric(row.objective_values?.[name])}</td>`).join("")}
        <td>${metric(row.metrics?.mass_g)} g</td>
        <td>$${metric(row.metrics?.estimated_unit_cost_usd)}</td>
        <td>${row.feasible === false ? "constraint failure" : "constraints pass"}</td>
        <td>${row.id && row.id !== "baseline"
          ? `<button class="btn small" data-preview-candidate="${esc(row.id)}">Preview</button>
             <button class="btn small" data-apply-candidate="${esc(row.id)}">Apply…</button>` : "current design"}</td>
      </tr>`).join("")}</tbody></table>` : `<div class="empty-note">Select at least one design.</div>`;
  holder.innerHTML = `<div class="movement-filter-row">${chooser}</div>${table}
    <p class="design-note">Preview builds a revision-scoped GLB without changing the live design. Applying opens a separate confirmation and a new undoable revision.</p>`;
}

/* ------------------------------------------------------ movement database */
function movementParams() {
  const params = new URLSearchParams();
  const q = $("#movementSearch")?.value?.trim();
  const maker = $("#movementMaker")?.value;
  const type = $("#movementType")?.value;
  if (q) params.set("q", q);
  if (maker) params.set("maker", maker);
  if (type) params.set("type", type);
  for (const [id, key] of [["movementMinDiameter", "min_diameter"],
    ["movementMaxDiameter", "max_diameter"],
    ["movementMaxThickness", "max_thickness"]]) {
    const value = $(`#${id}`)?.value;
    if (value !== "" && value !== undefined) params.set(key, value);
  }
  if ($("#movementUnknown")?.checked) params.set("include_unknown", "1");
  return params;
}

function populateMovementFilters() {
  const maker = $("#movementMaker");
  if (!maker || !MOVEMENT.catalog) return;
  const current = maker.value;
  const makers = MOVEMENT.catalog.makers || [];
  maker.innerHTML = `<option value="">All makers</option>`
    + makers.map((name) => `<option value="${esc(name)}">${esc(name)}</option>`).join("");
  if (makers.includes(current)) maker.value = current;
}

async function loadMovementCatalog() {
  if (MOVEMENT.catalog) { populateMovementFilters(); renderMovement(); return; }
  MOVEMENT.error = "";
  try {
    const data = await api(withSession("/api/movements"));
    MOVEMENT.catalog = data;
    MOVEMENT.list = data.movements || [];
    populateMovementFilters();
    renderMovement();
  } catch (error) {
    MOVEMENT.error = error.message;
    renderMovement();
  }
}

async function loadMovementList() {
  MOVEMENT.error = "";
  try {
    const params = movementParams();
    const data = await api(withSession(`/api/movements?${params.toString()}`));
    MOVEMENT.catalog = data;
    MOVEMENT.list = data.movements || [];
    populateMovementFilters();
    renderMovement();
  } catch (error) {
    MOVEMENT.error = error.message;
    renderMovement();
  }
}

async function loadMovementDetail(id) {
  if (!id) return;
  MOVEMENT.loading = true;
  MOVEMENT.error = "";
  MOVEMENT.proposal = null;
  renderMovement();
  try {
    const data = await api(withSession(`/api/movements/detail?id=${encodeURIComponent(id)}`));
    MOVEMENT.detail = data;
  } catch (error) {
    MOVEMENT.error = error.message;
  } finally {
    MOVEMENT.loading = false;
    renderMovement();
  }
}

function movementValue(value) {
  if (value === null || value === undefined || value === "") return "not published";
  if (typeof value === "boolean") return value ? "yes" : "no";
  if (typeof value === "number") return `${fmt(value, 2)}`;
  return String(value);
}

function fitStatus(status) {
  const key = String(status || "UNKNOWN").toLowerCase();
  return `<span class="fit-status ${esc(key)}">${esc(status)}</span>`;
}

function movementSpecRows(record, provenance) {
  const groups = [
    ["Identity", ["manufacturer", "calibre", "line", "type", "winding",
      "manual_winding", "hacking"]],
    ["Envelope", ["diameter_mm", "case_fitting_diameter_mm", "overall_diameter_mm",
      "thickness_mm", "stack_height_mm"]],
    ["Dial and hands", ["dial.max_diameter_mm", "dial.date_position",
      "hand_fitting.hour_mm", "hand_fitting.minute_mm", "hand_fitting.second_mm"]],
    ["Stem", ["stem.position_height_mm", "stem.diameter_mm",
      "stem.distance_from_centre_mm"]],
    ["Performance", ["frequency_bph", "power_reserve_h", "jewels",
      "accuracy_s_per_day", "lift_angle_deg"]],
    ["Mounting", ["mounting.method", "mounting.caseback"]],
  ];
  const label = (path) => path.split(".").pop().replaceAll("_", " ");
  return groups.map(([title, paths]) => {
    const rows = paths.map((path) => {
      const prov = provenance[path] || { value: getPath(record, path), status: "unknown" };
      const source = prov.source_url
        ? ` <a href="${esc(prov.source_url)}" target="_blank" rel="noreferrer noopener">source</a>` : "";
      return `<tr>
        <th title="${esc(path)}">${esc(label(path))}</th>
        <td>${esc(movementValue(prov.value))}</td>
        <td><span class="prov ${esc(prov.status || "unknown")}">${esc(prov.status || "unknown")}</span>${source}
          ${prov.note ? `<div class="movement-note">${esc(prov.note)}</div>` : ""}</td>
      </tr>`;
    }).join("");
    return `<table class="movement-table"><caption>${esc(title)}</caption><tbody>${rows}</tbody></table>`;
  }).join("");
}

function envelopeSvg(envelope) {
  if (!envelope) return "";
  const caseD = Number(envelope.case_diameter_mm) || 40;
  const moveD = Number(envelope.movement_diameter_mm) || 26;
  const caseT = Number(envelope.case_thickness_mm) || 11;
  const moveT = Number(envelope.movement_thickness_mm) || 4.6;
  const scale = 58 / Math.max(1, caseD / 2);
  const cx = 64, cy = 64;
  const rCase = (caseD / 2) * scale;
  const rMove = (moveD / 2) * scale;
  const sideScale = Math.min(6, 90 / Math.max(caseT, 1));
  const x0 = 150, y0 = 24;
  return `<svg class="envelope-svg" viewBox="0 0 244 130" role="img"
      aria-label="Case and movement envelope preview">
    <title>Case Ø${fmt(caseD)} mm against movement Ø${fmt(moveD)} mm</title>
    <circle cx="${cx}" cy="${cy}" r="${rCase}" fill="none" stroke="#59677a" stroke-width="1.4"/>
    <circle cx="${cx}" cy="${cy}" r="${rMove}" fill="rgba(100,168,232,.14)" stroke="#64a8e8" stroke-width="1.2"/>
    <line x1="${cx - rCase}" y1="${cy}" x2="${cx + rCase}" y2="${cy}" stroke="#59677a" stroke-dasharray="3 3"/>
    <text x="${cx}" y="${cy + rCase + 12}" text-anchor="middle" class="svg-label">top · Ø case / movement</text>
    <rect x="${x0}" y="${y0}" width="${caseT * sideScale}" height="76" fill="none" stroke="#59677a" stroke-width="1.4"/>
    <rect x="${x0}" y="${y0 + 6}" width="${moveT * sideScale}" height="64" fill="rgba(100,168,232,.14)" stroke="#64a8e8" stroke-width="1.2"/>
    <text x="${x0}" y="${y0 + 90}" class="svg-label">section · H case / movement</text>
  </svg>`;
}

function movementDetailHtml() {
  const detail = MOVEMENT.detail;
  if (!detail) return MOVEMENT.loading
    ? `<p class="movement-empty">Loading movement record…</p>`
    : `<p class="movement-empty">Choose a calibre to inspect its published specifications, provenance and fit.</p>`;
  const record = detail.record;
  const fit = detail.compatibility || {};
  const checks = fit.checks || [];
  const envelope = detail.envelope || {};
  const proposal = MOVEMENT.proposal;
  const counts = fit.counts || {};
  const suggested = proposal?.suggested_changes || [];
  const identity = proposal?.variant?.changes || [];
  const missing = detail.missing || [];
  const sources = detail.sources || record.sources_full || [];
  const diffRows = [...identity, ...suggested].map((change) =>
    `<tr><td>${esc(change.path)}</td><td>${esc(movementValue(change.from))}</td>
      <td>${esc(movementValue(change.to))}</td>
      <td>${esc(change.reason || change.note || "")}</td></tr>`).join("");
  return `<section class="movement-record">
    <div class="movement-record-head">
      <div>
        <h3>${esc(record.name)}</h3>
        <p>${esc(record.manufacturer)} · ${esc(record.type)} · ${esc(record.status)} ·
          verified ${esc(record.verified_at)}</p>
      </div>
      <div class="movement-score">${fitStatus(fit.overall)}
        <span>${counts.PASS || 0} pass · ${counts.WARNING || 0} warn ·
          ${counts.FAIL || 0} fail · ${counts.UNKNOWN || 0} unknown</span></div>
    </div>
    ${MOVEMENT.loading ? `<p class="provenance">Refreshing…</p>` : ""}
    ${movementSpecRows(record, detail.provenance || {})}
    <div class="movement-envelope-wrap">
      ${envelopeSvg(envelope)}
      <dl class="movement-envelope">
        <div><dt>Case</dt><dd>Ø${fmt(envelope.case_diameter_mm)} × ${fmt(envelope.case_thickness_mm)} mm</dd></div>
        <div><dt>Case bore</dt><dd>Ø${fmt(envelope.case_bore_mm)} mm</dd></div>
        <div><dt>Movement</dt><dd>Ø${fmt(envelope.movement_diameter_mm)} × ${fmt(envelope.movement_thickness_mm)} mm</dd></div>
        <div><dt>Radial clearance</dt><dd>${envelope.radial_clearance_mm === null ? "unknown" : `${fmt(envelope.radial_clearance_mm, 3)} mm`}</dd></div>
      </dl>
    </div>
    <h4>Compatibility report</h4>
    <div class="fit-list">${checks.map((check) => `<article class="fit-check ${esc(check.status.toLowerCase())}">
      <div class="fit-check-head">${fitStatus(check.status)} <b>${esc(check.label)}</b></div>
      <p>${esc(check.detail)}</p>
      ${check.fix ? `<div class="movement-note">Suggested: ${esc(check.fix.path)} → ${esc(movementValue(check.fix.to))}</div>` : ""}
    </article>`).join("")}</div>
    <p class="movement-assumption">${esc(fit.assumptions?.note || "")}</p>
    <div class="movement-propose">
      <div>
        <h4>Adopt this calibre</h4>
        <p class="movement-note">The proposal is a diff only. Nothing changes until you confirm below; applying runs the solver, mesh builder and verifier.</p>
      </div>
      <button class="btn small primary" data-movement-propose="${esc(record.id)}">Review fit proposal</button>
    </div>
    ${proposal ? `<div class="movement-proposal">
      <div class="movement-proposal-head">
        <b>Spec diff</b>
        <span class="provenance">baseline ${esc(String(proposal.baseline?.checksum || "").slice(0, 12))}</span>
      </div>
      ${diffRows ? `<table class="movement-diff"><thead><tr><th>path</th><th>from</th><th>to</th><th>reason</th></tr></thead><tbody>${diffRows}</tbody></table>`
        : `<p class="movement-note">The identity fields already match this record. No fit correction is suggested.</p>`}
      ${(proposal.notes || []).map((note) => `<p class="movement-note">${esc(note)}</p>`).join("")}
      ${(proposal.locked || []).length ? `<p class="movement-note locked-note">Locked fields are skipped: ${proposal.locked.map((item) => esc(item.path)).join(", ")}</p>` : ""}
      <p class="movement-note">Consequences: ${suggested.length} fit correction(s) would be applied with the movement identity. ${counts.FAIL || 0} check(s) currently fail and ${counts.UNKNOWN || 0} cannot be confirmed from the retained source.</p>
      <div class="movement-proposal-actions">
        <button class="btn small primary" data-movement-apply="fit">Apply movement + fit changes</button>
        <button class="btn small" data-movement-apply="identity">Apply movement identity only</button>
        <button class="btn small ghost" data-movement-cancel>Cancel</button>
      </div>
    </div>` : ""}
    <details class="movement-missing" ${missing.length ? "open" : ""}>
      <summary>${missing.length} unpublished field(s)</summary>
      <ul>${missing.map((item) => `<li><b>${esc(item.path)}</b> — ${esc(item.reason)}</li>`).join("")}</ul>
    </details>
    <h4>Sources</h4>
    <ul class="movement-sources">${sources.map((source) => `<li>
      <a href="${esc(source.url)}" target="_blank" rel="noreferrer noopener">${esc(source.name)}</a>
      <span>${esc(source.publisher)}${source.revision ? ` · ${esc(source.revision)}` : ""}${source.published ? ` · ${esc(source.published)}` : ""} · retrieved ${esc(source.retrieved)}</span>
      ${source.note ? `<small>${esc(source.note)}</small>` : ""}
    </li>`).join("")}</ul>
    <p class="movement-note">${esc(detail.current?.note || "")} Compatibility uses the current design as the baseline; it is not a tolerance stack or a solid-kernel interference check.</p>
  </section>`;
}

function renderMovement() {
  const list = $("#movementList");
  const detail = $("#movementDetail");
  if (!list || !detail) return;
  if (MOVEMENT.error) {
    list.innerHTML = `<p class="movement-empty bad">${esc(MOVEMENT.error)}</p>`;
    detail.innerHTML = "";
    return;
  }
  if (!MOVEMENT.catalog) {
    list.innerHTML = `<p class="movement-empty">Loading the sourced movement catalogue…</p>`;
    detail.innerHTML = "";
    return;
  }
  const items = MOVEMENT.list || [];
  list.innerHTML = items.length ? items.map((item) => {
    const active = MOVEMENT.detail?.record?.id === item.id;
    return `<button class="movement-item ${active ? "active" : ""}" data-movement="${esc(item.id)}">
      <span class="movement-item-name"><b>${esc(item.manufacturer)}</b> ${esc(item.calibre)}</span>
      <span class="movement-item-meta">${esc(item.type)} · Ø${fmt(item.diameter_mm)} × ${fmt(item.thickness_mm)} mm
        · ${item.missing_count} unpublished</span>
      <span class="prov ${esc(item.status)}">${esc(item.status)}</span>
    </button>`;
  }).join("") : `<p class="movement-empty">No records match those filters. Widen the dimensions or include unknown values.</p>`;
  detail.innerHTML = movementDetailHtml();
}

async function proposeMovement() {
  const id = MOVEMENT.detail?.record?.id;
  if (!id) return;
  MOVEMENT.loading = true;
  try {
    MOVEMENT.proposal = await api("/api/movements/propose", { id });
  } catch (error) {
    toast(error.message, true);
  } finally {
    MOVEMENT.loading = false;
    renderMovement();
  }
}

async function applyMovementProposal(scope) {
  const proposal = MOVEMENT.proposal;
  if (!proposal) return;
  const merged = new Map();
  const push = (change) => {
    if (!change?.path) return;
    merged.set(change.path, {
      path: change.path,
      from: change.from,
      to: change.to,
      note: change.reason || change.note || "movement fit",
    });
  };
  (proposal.variant?.changes || []).forEach(push);
  if (scope === "fit") (proposal.suggested_changes || []).forEach(push);
  const changes = [...merged.values()];
  if (!changes.length) { toast("no spec changes are needed for this movement", true); return; }
  const record = proposal.movement;
  const label = scope === "fit"
    ? `Fit ${record.name} with corrections`
    : `Fit ${record.name}`;
  setLoading(true, "applying movement through solver and verifier");
  try {
    const payload = await api("/api/agent/variant", {
      variant: {
        label,
        rationale: `Adopt ${record.manufacturer} ${record.calibre} from the sourced movement database.`,
        changes,
      },
      mode: "safe",
    });
    applyAgentResult(payload);
    CHAT.messages.push(assistantFromAgent(payload));
    renderChat();
    MOVEMENT.proposal = null;
    await loadMovementDetail(record.id);
    const failed = Number(payload.verification?.fail ?? payload.summary?.fail ?? 0);
    toast(failed
      ? `applied as a new design version with ${failed} failing check(s); inspect Checks`
      : "applied as a new design version; use undo to return to the previous version");
  } catch (error) {
    toast(error.message, true);
  } finally {
    setLoading(false);
  }
}

/* ------------------------------------------------------- AI providers */
function providerById(id) {
  return (DESIGN.providers || []).find((item) => item.id === id) || null;
}

function providerModal() {
  return $("#providerModal");
}

async function openProviders() {
  const modal = providerModal();
  if (modal) modal.hidden = false;
  const status = $("#providerStatus");
  if (status) {
    status.className = "provider-status";
    status.textContent = "Loading provider catalogue…";
  }
  try {
    const data = await api("/api/providers");
    DESIGN.providers = data.providers || [];
    const active = data.active || {};
    DESIGN.providerKey = Boolean(active.session_key);
    const provider = active.provider || data.settings?.provider || "openai-compatible";
    selectProvider(provider, {
      endpoint: data.settings?.url || active.url || "",
      model: data.settings?.model || active.model || "",
      vision: data.settings?.vision_model ?? active.vision_model ?? "",
      keepStatus: false,
    });
    renderProviderSecurity(data.security);
  } catch (error) {
    if (status) {
      status.className = "provider-status bad";
      status.textContent = `Could not load providers: ${error.message}`;
    }
  }
}

function closeProviders() {
  const modal = providerModal();
  if (modal) modal.hidden = true;
  const keyInput = $("#providerKey");
  if (keyInput) keyInput.value = "";
}

function renderProviderSecurity(security) {
  DESIGN.security = security || null;
  const hint = $("#settingsHint");
  if (!hint) return;
  hint.textContent = security?.persisted === false
    ? "Keys stay in server memory for this browser session and are never persisted."
    : "Endpoints and models are non-secret. Use AI provider for session keys.";
}

function renderProviderChoices() {
  const holder = $("#providerChoices");
  if (!holder) return;
  holder.innerHTML = (DESIGN.providers || []).map((provider) => {
    const active = provider.id === DESIGN.provider;
    const keyed = active && DESIGN.providerKey;
    return `<button type="button" class="provider-choice ${active ? "active" : ""}"
      data-provider="${esc(provider.id)}">
      <b>${esc(provider.name || provider.id)}</b>
      <span>${esc(provider.model || "")}</span>
      ${keyed ? '<span class="keyed">key held for this session</span>' : ""}
    </button>`;
  }).join("");
}

function providerDatalist(provider) {
  const list = $("#providerModels");
  if (!list) return;
  list.innerHTML = (provider?.models || []).map((model) =>
    `<option value="${esc(model)}"></option>`).join("");
}

function selectProvider(id, options = {}) {
  const provider = providerById(id) || providerById("openai-compatible") || {
    id, name: id, url: "", model: "", vision_model: "", models: [], capabilities: {},
  };
  DESIGN.provider = provider.id;
  DESIGN.capabilityKey = "";
  renderProviderChoices();
  providerDatalist(provider);
  const endpoint = $("#providerEndpoint");
  const model = $("#providerModel");
  const vision = $("#providerVision");
  if (endpoint) endpoint.value = options.endpoint || provider.url || "";
  if (model) model.value = options.model || provider.model || "";
  if (vision) vision.value = options.vision ?? provider.vision_model ?? "";
  renderProviderCaps(provider.capabilities);
  if (!options.keepStatus) {
    const status = $("#providerStatus");
    if (status) {
      status.className = "provider-status";
      status.textContent = provider.note || "Enter a key to test this provider.";
    }
  }
}

function renderProviderCaps(caps) {
  const holder = $("#providerCaps");
  if (!holder) return;
  const names = ["streaming", "tools", "json", "images"];
  holder.innerHTML = names.map((name) => {
    const entry = (caps || {})[name];
    const on = typeof entry === "object" && entry !== null
      ? Boolean(entry.supported) : Boolean(entry);
    const detail = typeof entry === "object" && entry !== null
      ? (entry.source || "") : "";
    return `<span class="cap ${on ? "on" : ""}" title="${esc(detail)}">${name}</span>`;
  }).join("");
}

function providerPayload() {
  const keyInput = $("#providerKey");
  return {
    provider: DESIGN.provider || "openai-compatible",
    endpoint: $("#providerEndpoint")?.value?.trim() || "",
    model: $("#providerModel")?.value?.trim() || "",
    vision_model: $("#providerVision")?.value?.trim() || "",
    key: keyInput?.value?.trim() || "",
  };
}

function setProviderStatus(text, tone = "") {
  const status = $("#providerStatus");
  if (!status) return;
  status.className = `provider-status ${tone}`.trim();
  status.textContent = text;
}

function applyProviderPayload(data) {
  if (data.providers) DESIGN.providers = data.providers;
  const active = data.active || {};
  DESIGN.providerKey = Boolean(active.session_key ?? data.session_key);
  if (active.provider) DESIGN.provider = active.provider;
  DESIGN.capabilities = data.capabilities || {};
  renderProviderChoices();
  renderProviderCaps(DESIGN.capabilities);
  renderProviderSecurity(data.security);
}

function capabilityKey(payload) {
  return `${payload.provider || ""}|${payload.endpoint || ""}|${payload.model || ""}`;
}

async function testProvider() {
  const payload = providerPayload();
  if (!payload.key && !DESIGN.providerKey) {
    setProviderStatus("Enter an API key before testing.", "bad");
    return;
  }
  setProviderStatus("Testing the endpoint with small live requests…");
  const button = $("#btnProviderTest");
  if (button) button.disabled = true;
  try {
    const result = await api("/api/providers/test", payload);
    if (result.ok) {
      // Remember measured capabilities so the preset flags returned by a later
      // save do not overwrite what the probe actually observed.
      DESIGN.capabilityKey = capabilityKey(payload);
      DESIGN.capabilities = result.capabilities || {};
    }
    renderProviderCaps(result.capabilities || {});
    if (result.ok) {
      setProviderStatus(`Connection works. Endpoint ${result.endpoint} · model ${result.model}.`, "good");
    } else {
      setProviderStatus(`Connection failed: ${result.error || "no detail"}`, "bad");
    }
    return result;
  } catch (error) {
    setProviderStatus(`Connection failed: ${error.message}`, "bad");
    return null;
  } finally {
    if (button) button.disabled = false;
  }
}

async function saveProvider() {
  const payload = providerPayload();
  if (!payload.endpoint) {
    setProviderStatus("Enter an endpoint URL for this provider.", "bad");
    return;
  }
  if (!payload.key && !DESIGN.providerKey) {
    setProviderStatus("Enter an API key to hold for this session.", "bad");
    return;
  }
  setProviderStatus("Saving the non-secret endpoint settings and the session key…");
  try {
    const data = await api("/api/providers/save", payload);
    const measured = DESIGN.capabilities;
    const measuredKey = DESIGN.capabilityKey;
    applyProviderPayload(data);
    if (measuredKey && measuredKey === capabilityKey(payload)) {
      DESIGN.capabilities = measured;
      renderProviderCaps(measured);
    }
    const keyInput = $("#providerKey");
    if (keyInput) keyInput.value = "";
    const useLlm = $("#useLlm");
    const ready = Boolean(data.llm?.key_configured || data.session_key);
    if (useLlm) useLlm.checked = ready;
    if (ready) {
      CHAT.engine = "llm ready";
      renderChat();
    }
    setProviderStatus(
      "Saved. The endpoint and model are persisted; the key lives only in server memory for this session.",
      "good");
    toast("AI provider saved for this session");
  } catch (error) {
    setProviderStatus(`Save failed: ${error.message}`, "bad");
  }
}

async function removeProvider() {
  setProviderStatus("Removing the session key…");
  try {
    const data = await api("/api/providers/remove", {});
    applyProviderPayload(data);
    const keyInput = $("#providerKey");
    if (keyInput) keyInput.value = "";
    const useLlm = $("#useLlm");
    const ready = Boolean(data.llm?.key_configured);
    if (useLlm) useLlm.checked = ready;
    CHAT.engine = ready ? "llm ready" : "local rules";
    renderChat();
    setProviderStatus(
      ready
        ? "Session key removed. The endpoint stays saved; an environment key still provides the model."
        : "Session key removed. The endpoint and model stay saved; chat falls back to the deterministic engine.",
      "good");
    toast("session key removed");
  } catch (error) {
    setProviderStatus(`Remove failed: ${error.message}`, "bad");
  }
}

/* --------------------------------------------------------------- header */
function renderHeader() {
  if (!STATE) return;
  const s = STATE.summary || {};
  const spec = STATE.spec.data;
  const name = spec.meta?.name || "Untitled";
  const chips = [];
  chips.push(`<span class="chip info"><b>${esc(name)}</b>${esc(spec.case.material)}</span>`);
  if (s.fail) chips.push(`<span class="chip bad"><b>${s.fail}</b> failed</span>`);
  else chips.push(`<span class="chip good"><b>0</b> failures</span>`);
  chips.push(`<span class="chip"><b>${s.calculated ?? 0}</b> calculated</span>`);
  chips.push(`<span class="chip"><b>${s.estimated ?? 0}</b> estimated</span>`);
  chips.push(`<span class="chip"><b>${s.needs_test ?? 0}</b> needs test</span>`);
  chips.push(`<span class="chip"><b>${fmt(STATE.scene.mass_g, 1)}</b> g</span>`);
  chips.push(`<span class="chip">${(STATE.scene.triangles || 0).toLocaleString()} tris</span>`);
  const checksum = spec.derived?.checksum || "";
  if (checksum) chips.push(`<span class="chip" title="spec checksum">${esc(checksum.slice(0, 10))}</span>`);
  $("#summaryBar").innerHTML = chips.join("");

  const project = $("#projectName");
  if (project) project.textContent = name;
  const saving = appliedRevision > renderedRevision;
  const save = $("#saveStatus");
  if (save) {
    save.textContent = saving ? "syncing geometry" : "saved";
    save.classList.toggle("dirty", saving);
  }
  const header = $("#headerStatus");
  if (header) {
    header.textContent = `revision ${appliedRevision || state.revision || 0} · version ${spec.meta?.version ?? 1} · ${checksum.slice(0, 12) || "no checksum"}`;
  }
  const undo = $("#btnUndo");
  const redo = $("#btnRedo");
  if (undo) undo.disabled = !Boolean(STATE.session?.can_undo);
  if (redo) redo.disabled = !Boolean(STATE.session?.can_redo);
  const ai = $("#globalAiStatus");
  if (ai) {
    const provider = CHAT.engine || AGENT.engine || "local rules";
    ai.textContent = provider.length > 22 ? `${provider.slice(0, 21)}…` : provider;
    ai.classList.toggle("ready", provider !== "local rules");
    ai.title = provider === "local rules"
      ? "Local deterministic engine is always available"
      : provider;
  }
  const engineering = $("#engineeringToolStatus");
  if (engineering) engineering.textContent = s.fail
    ? `${s.fail} failing check${s.fail === 1 ? "" : "s"} · solver-backed`
    : "all checks pass · solver-backed";
  const designStatus = $("#designToolStatus");
  if (designStatus) designStatus.textContent = `mesh revision ${appliedRevision || state.revision || 0}`;
}

function renderFacts() {
  if (!STATE) return;
  const spec = STATE.spec.data;
  const derived = spec.derived || {};
  const comps = Object.keys(spec.movement.complications || {});
  const stack = derived.stack || {};
  const rows = [
    ["Case", `Ø ${fmt(spec.case.diameter_mm, 1)} × ${fmt(spec.case.thickness_mm, 1)} mm`],
    ["Lug-to-lug", `${fmt(spec.case.lug_to_lug_mm, 1)} mm`],
    ["Lug width", `${fmt(spec.case.lug_width_mm, 1)} mm`],
    ["Water", `${spec.case.water_resistance_m} m`],
    ["Crystal", `${spec.case.crystal}, ${spec.case.crystal_type}`],
    ["Dial", `${spec.dial.color} · ${spec.dial.index_style}`],
    ["Movement", `${spec.movement.kind}${spec.movement.kind === "mechanical" ? ", " + spec.movement.winding : ""}`],
    ["Complications", comps.length ? comps.join(", ") : "none"],
    ["Stack margin", `${fmt(stack.margin_mm, 2)} mm`],
    ["Mass (mesh)", `${fmt(STATE.scene.mass_g, 2)} g`],
    ["Parts", `${STATE.scene.parts}`],
    ["Triangles", `${(STATE.scene.triangles || 0).toLocaleString()}`],
    ["Checksum", (spec.derived?.checksum || "").slice(0, 12)],
  ];
  $("#facts").innerHTML = rows
    .map(([key, value]) => `<dt>${esc(key)}</dt><dd>${esc(value)}</dd>`).join("");

  const report = (STATE.report || []).slice(-7);
  $("#why").innerHTML = report.length
    ? report.map((row) => {
        let value = row.value;
        if (value && typeof value === "object") value = JSON.stringify(value);
        value = String(value ?? "").slice(0, 64);
        return `<div><b>${esc(row.field || "")}</b> ${esc(value)}</div>`;
      }).join("")
    : `<div>No derivation report yet.</div>`;
}

function renderExamples() {
  const holder = $("#examples");
  if (!SCHEMA) return;
  holder.innerHTML = (SCHEMA.ideas || []).map((item) =>
    `<button data-preset="${esc(item.id)}" title="${esc(item.prompt)}">${esc(item.name)}</button>`
  ).join("");
}

/* ----------------------------------------------------------------- agent */
const cloneSpec = (spec) => {
  try { return structuredClone(spec); } catch { return JSON.parse(JSON.stringify(spec)); }
};

function currentMode() {
  const select = $("#autonomy");
  const value = select && select.value;
  return AGENT_MODES.includes(value) ? value : "autonomous";
}

function seedChat() {
  if (CHAT.messages.length || !STATE) return;
  const spec = STATE.spec.data;
  const c = spec.case || {};
  const m = spec.movement || {};
  CHAT.messages.push({
    role: "assistant",
    lead: `Hello. This is currently a **${fmt(c.diameter_mm, 0)} mm `
      + `${c.material || "steel"} ${m.kind || "mechanical"} watch**. `
      + "You can ask me about the design, materials or movements, or ask for a "
      + "specific change. I only modify the watch through validated CAD tools, "
      + "and every change is solved and verified before it is committed.",
    notes: ["The deterministic solver owns every dimension. Nothing is committed unless verification succeeds."],
    suggestions: [
      "Make it 40 mm titanium with a dive bezel",
      "Add a date and a chronograph",
      "Critique this design and suggest alternatives",
      "Fix the failing checks",
    ],
  });
}

function setAgentRunning(on) {
  AGENT.running = Boolean(on);
  const stop = $("#btnStop");
  if (stop) stop.hidden = !AGENT.running;
  const send = $("#btnParse");
  if (send) send.disabled = AGENT.running;
  const mode = $("#autonomy");
  if (mode) mode.disabled = AGENT.running;
  renderAgentStatus();
}

function renderSessionChip() {
  const chip = $("#sessionChip");
  if (!chip) return;
  const id = AGENT.sessionId || currentSession();
  chip.textContent = String(id).replace(/^web-/, "").slice(0, 12);
  chip.title = `Session ${id}: its conversation, history and design memory are isolated`;
}

/* ------------------------------------------------------------ conversations */
function rememberConversation(conversation) {
  try {
    if (!conversation) return;
    if (conversation.id) window.localStorage?.setItem("watchcad.conversation", conversation.id);
    if (conversation.design_session) {
      window.localStorage?.setItem("watchcad.session", conversation.design_session);
    }
  } catch { /* private mode or a non-browser harness */ }
}

function relativeTime(stamp) {
  const seconds = Number(stamp) || 0;
  if (!seconds) return "";
  const delta = Math.max(0, Date.now() / 1000 - seconds);
  if (delta < 60) return "just now";
  if (delta < 3600) return `${Math.round(delta / 60)}m ago`;
  if (delta < 86400) return `${Math.round(delta / 3600)}h ago`;
  return `${Math.round(delta / 86400)}d ago`;
}

async function loadConversations(query = "") {
  try {
    const suffix = query ? `?q=${encodeURIComponent(query)}` : "";
    const data = await api(`/api/conversations${suffix}`);
    CHAT.conversations = data.conversations || [];
    renderConversationList();
  } catch (error) {
    const holder = $("#conversationList");
    if (holder) holder.innerHTML = `<div class="empty-note bad">${esc(error.message)}</div>`;
  }
}

function renderConversationList() {
  const holder = $("#conversationList");
  if (!holder) return;
  const rows = CHAT.conversations || [];
  holder.innerHTML = rows.length ? rows.map((conversation) => {
    const active = conversation.id === CHAT.conversationId;
    const title = conversation.title || "Untitled conversation";
    const count = Number(conversation.message_count || 0);
    return `<div class="conversation-row ${active ? "active" : ""}" data-conversation="${esc(conversation.id)}">
      <button class="conversation-main" data-open-conversation="${esc(conversation.id)}" title="${esc(title)}">
        <span class="conversation-title">${esc(title)}</span>
        <span class="conversation-meta">${count} message${count === 1 ? "" : "s"} · ${esc(relativeTime(conversation.updated_at))}</span>
      </button>
      <button class="conversation-act" data-rename-conversation="${esc(conversation.id)}" title="Rename conversation">✎</button>
      <button class="conversation-act danger" data-delete-conversation="${esc(conversation.id)}" title="Delete conversation">×</button>
    </div>`;
  }).join("") : `<div class="empty-note">No saved conversations yet. Say something to start one.</div>`;
}

function toggleChatHistory() {
  CHAT.historyOpen = !CHAT.historyOpen;
  const box = $("#chatSidebar");
  if (box) box.hidden = !CHAT.historyOpen;
  const button = $("#btnChats");
  if (button) button.classList.toggle("active", CHAT.historyOpen);
  if (CHAT.historyOpen) loadConversations($("#conversationSearch")?.value || "");
}

function timelineFromToolEvents(events) {
  return (events || []).map((event, index) => ({
    seq: index + 1,
    kind: "tool",
    title: event.name,
    status: event.ok ? "ok" : "error",
    iteration: 0,
    detail: (event.result || {}).message || (event.result || {}).error || "",
    data: { tool: event.name, arguments: event.arguments || {} },
  }));
}

function messagesFromRows(rows) {
  return (rows || []).filter((row) => row.role === "user" || row.role === "assistant")
    .map((row) => {
      const data = row.data || {};
      const changes = data.changes || [];
      return {
        id: row.id,
        role: row.role,
        text: row.content || "",
        lead: row.content || "",
        attachments: data.images || [],
        patches: changes.map((change) => ({
          path: change.path, value: change.to, from: change.from,
        })),
        suggestions: data.suggestions || [],
        engine: data.engine || "",
        status: data.status || "",
        bad: ["error", "cancelled", "timeout"].includes(data.status),
        undoable: false,
      };
    });
}

function applyConversationPayload(data) {
  const conversation = data.conversation || {};
  CHAT.conversationId = conversation.id || CHAT.conversationId;
  rememberConversation(conversation);
  if (data.session?.session_id) rememberSession(data.session.session_id);
  if (data.snapshot) applyState(data.snapshot);
  CHAT.messages = messagesFromRows(data.messages);
  if (!CHAT.messages.length) {
    CHAT.messages = [];
    seedChat();
  }
  const lastAssistant = [...CHAT.messages].reverse()
    .find((message) => message.role === "assistant" && message.engine);
  CHAT.engine = lastAssistant?.engine || CHAT.engine || "local rules";
  CHAT.pending = data.pending || null;
  AGENT.pending = CHAT.pending;
  if (data.tool_events) {
    AGENT.events = timelineFromToolEvents(data.tool_events);
  }
  renderChat();
  renderAgent();
  renderConversationList();
}

async function openConversation(conversationId, options = {}) {
  if (AGENT.running) { toast("stop the current run first", true); return; }
  try {
    const data = await api(`/api/conversations/${encodeURIComponent(conversationId)}`);
    applyConversationPayload(data);
    if (CHAT.historyOpen && !options.keepSidebar) toggleChatHistory();
  } catch (error) {
    toast(error.message, true);
  }
}

async function newChat() {
  if (AGENT.running) { toast("stop the current run first", true); return; }
  try {
    const data = await api("/api/conversations", { title: "New conversation" });
    CHAT.messages = [];
    applyConversationPayload(data);
    CHAT.messages = [];
    seedChat();
    renderChat();
    const input = $("#prompt");
    if (input) { input.value = ""; input.focus?.(); }
    await loadConversations();
    toast("started a new conversation");
  } catch (error) {
    toast(error.message, true);
  }
}

async function renameConversation(conversationId) {
  const current = (CHAT.conversations || []).find((row) => row.id === conversationId);
  const title = window.prompt?.("Conversation title", current?.title || "");
  if (title === null || title === undefined || !String(title).trim()) return;
  try {
    await api(`/api/conversations/${encodeURIComponent(conversationId)}/rename`,
      { title: String(title).trim() });
    await loadConversations($("#conversationSearch")?.value || "");
  } catch (error) {
    toast(error.message, true);
  }
}

async function deleteConversation(conversationId) {
  const confirmed = window.confirm?.("Delete this conversation? The design stays in its saved session.")
    ?? true;
  if (!confirmed) return;
  try {
    await api(`/api/conversations/${encodeURIComponent(conversationId)}/delete`, {});
    if (conversationId === CHAT.conversationId) {
      CHAT.conversationId = null;
      try { window.localStorage?.removeItem("watchcad.conversation"); } catch { /* ignore */ }
      CHAT.messages = [];
      seedChat();
      renderChat();
    }
    await loadConversations($("#conversationSearch")?.value || "");
    toast("conversation deleted");
  } catch (error) {
    toast(error.message, true);
  }
}

async function initChat() {
  try {
    const settings = await api("/api/settings");
    // A persisted endpoint without a usable credential is not a working model.
    // Auto-enable the model only when the server actually holds a key for this
    // session (or an environment key); otherwise the deterministic engine must
    // stay in charge instead of failing every turn with an HTTP error.
    const keyReady = Boolean(settings.llm?.key_configured || settings.session_key);
    const usable = Boolean(settings.llm?.configured && keyReady);
    if (usable && $("#useLlm")) $("#useLlm").checked = true;
    CHAT.engine = usable ? "llm ready" : "local rules";
  } catch { /* settings are optional */ }
  let saved = "";
  try { saved = window.localStorage?.getItem("watchcad.conversation") || ""; } catch { saved = ""; }
  if (saved) {
    try {
      const data = await api(`/api/conversations/${encodeURIComponent(saved)}`);
      applyConversationPayload(data);
      loadConversations();
      return;
    } catch { /* the saved transcript may have been deleted */ }
  }
  seedChat();
  loadConversations();
}

/* ------------------------------------------------------------ chat actions */
function lastStreamingMessage() {
  for (let i = CHAT.messages.length - 1; i >= 0; i -= 1) {
    if (CHAT.messages[i].streaming) return CHAT.messages[i];
  }
  return null;
}

let chatRenderTimer = 0;
function scheduleChatRender() {
  if (chatRenderTimer) return;
  chatRenderTimer = setTimeout(() => {
    chatRenderTimer = 0;
    renderChat();
  }, 45);
}

function handleChatEvent(event) {
  if (!event || !event.type) return;
  if (event.type === "start") {
    CHAT.runId = event.run_id || CHAT.runId;
    AGENT.sessionId = event.session_id || AGENT.sessionId;
    CHAT.status = "Thinking";
    renderChatStatus();
    return;
  }
  if (event.type === "notice") {
    CHAT.status = event.text || CHAT.status;
    renderChatStatus();
    return;
  }
  if (event.type === "status") {
    CHAT.status = event.text || CHAT.status;
    if (event.detail) CHAT.status += ` — ${event.detail}`;
    renderChatStatus();
    return;
  }
  if (event.type === "delta") {
    const draft = lastStreamingMessage();
    if (draft) {
      draft.lead += event.text || "";
      scheduleChatRender();
    }
    return;
  }
  if (event.type === "timeline" && event.event) {
    const item = event.event;
    if (!AGENT.events.some((known) => known.seq && known.seq === item.seq)) {
      AGENT.events.push(item);
    }
    renderAgentTimeline();
    return;
  }
  if (event.type === "error") {
    const detail = event.error?.message || event.error?.error || event.error || "assistant error";
    AGENT.error = detail;
    CHAT.status = detail;
    const draft = lastStreamingMessage();
    if (draft) {
      draft.bad = true;
      draft.lead = draft.lead || `The assistant could not finish: ${detail}`;
    }
    renderChat();
    return;
  }
  if (event.type === "done") {
    CHAT.status = event.status && event.status !== "complete"
      ? String(event.status).replaceAll("_", " ") : "";
    renderChatStatus();
  }
}

async function streamChatTurn(body, onEvent) {
  const revision = appliedRevision || Number(STATE?.revision || 0);
  if (revision && body.expected_revision === undefined) {
    body = { ...body, expected_revision: revision };
  }
  const response = await fetch("/api/chat", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-WatchCAD-Session": currentSession(),
    },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    let data = {};
    try { data = await response.json(); } catch { /* empty response */ }
    throw new Error(data.error || `HTTP ${response.status}`);
  }
  const type = String(response.headers?.get("content-type") || "");
  let final = null;
  const dispatch = (event) => {
    if (!event || !event.type) return;
    if (event.type === "result") final = event.result || null;
    onEvent(event);
  };
  const canStream = response.body && typeof response.body.getReader === "function"
    && typeof TextDecoder !== "undefined" && type.includes("text/event-stream");
  if (canStream) {
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let split = buffer.indexOf("\n\n");
      while (split >= 0) {
        parseSseFrame(buffer.slice(0, split), dispatch);
        buffer = buffer.slice(split + 2);
        split = buffer.indexOf("\n\n");
      }
    }
    buffer += decoder.decode();
    if (buffer.trim()) parseSseFrame(buffer, dispatch);
  } else if (type.includes("text/event-stream") || !type.includes("application/json")) {
    const text = await response.text();
    for (const frame of String(text).split(/\n\n/)) parseSseFrame(frame, dispatch);
  } else {
    const data = await response.json();
    dispatch({ type: "result", result: data });
    final = data;
  }
  return final;
}

function applyChatResult(payload, before) {
  if (!payload) return;
  if (payload.messages) CHAT.messages = messagesFromRows(payload.messages);
  if (!CHAT.messages.length) { seedChat(); }
  CHAT.engine = payload.engine || CHAT.engine;
  CHAT.pending = payload.pending || null;
  AGENT.pending = CHAT.pending;
  AGENT.result = {
    ...payload,
    response: payload.text || payload.response || "",
    verification_after: payload.verification_after || {},
  };
  AGENT.changes = payload.changes || [];
  AGENT.engine = payload.engine || AGENT.engine;
  AGENT.error = payload.error || null;
  if (payload.timeline) AGENT.events = payload.timeline;
  if (payload.session?.session_id) rememberSession(payload.session.session_id);
  if (payload.snapshot) applyState(payload.snapshot);
  if (payload.conversation) {
    CHAT.conversationId = payload.conversation.id || CHAT.conversationId;
    rememberConversation(payload.conversation);
  }
  if ((payload.changes || []).length && before) CHAT.prevSpec = before;
  renderChat();
  renderAgent();
  loadConversations($("#conversationSearch")?.value || "");
}

async function sendChat(textOverride, options = {}) {
  const regenerate = Boolean(options.regenerate);
  let prompt = String(textOverride ?? $("#prompt").value ?? "").trim();
  const images = CHAT.attachments.slice();
  if (!prompt && !images.length && !regenerate) {
    toast("ask a question or describe a change first", true);
    return;
  }
  if (AGENT.running) { toast("the assistant is already running", true); return; }
  const editId = options.editId !== undefined ? options.editId : CHAT.editId;
  if (!regenerate && editId) {
    const index = CHAT.messages.findIndex((message) => message.id === editId);
    if (index >= 0) CHAT.messages.splice(index);
  }
  if (regenerate) {
    while (CHAT.messages.length && CHAT.messages[CHAT.messages.length - 1].role === "assistant") {
      CHAT.messages.pop();
    }
    const lastUser = [...CHAT.messages].reverse().find((message) => message.role === "user");
    prompt = prompt || lastUser?.text || "";
  } else if (prompt || images.length) {
    CHAT.messages.push({ role: "user", text: prompt || "Please analyse this image.",
                         attachments: images });
  }
  const before = STATE && STATE.spec ? cloneSpec(STATE.spec) : null;
  const draft = { role: "assistant", lead: "", streaming: true };
  CHAT.messages.push(draft);

  CHAT.typing = true;
  CHAT.streaming = true;
  CHAT.status = "Thinking";
  CHAT.editId = "";
  CHAT.attachments = [];
  AGENT.events = [];
  AGENT.result = null;
  AGENT.changes = [];
  AGENT.variants = [];
  AGENT.pending = null;
  AGENT.error = null;
  if ($("#prompt")) $("#prompt").value = "";
  renderChat();
  renderAgent();
  renderAttachments();
  setAgentRunning(true);
  try {
    const payload = await streamChatTurn({
      conversation_id: CHAT.conversationId || "",
      message: prompt,
      images,
      mode: currentMode(),
      use_llm: Boolean($("#useLlm") && $("#useLlm").checked),
      stream: true,
      regenerate,
      edit_message_id: regenerate ? "" : (editId || ""),
      component: CRITIC.focus,
    }, handleChatEvent);
    if (!payload) throw new Error(AGENT.error || "the assistant returned no result");
    applyChatResult(payload, before);
    const applied = Number((payload.changes || []).length);
    if (payload.pending && payload.pending.token) {
      toast("changes are staged for your approval");
    } else if (applied) {
      toast(`applied ${applied} validated change${applied === 1 ? "" : "s"}`);
    }
  } catch (error) {
    const transient = lastStreamingMessage();
    if (transient) {
      transient.streaming = false;
      transient.bad = true;
      transient.lead = transient.lead || `The assistant could not finish: ${error.message}`;
    }
    AGENT.error = error.message;
    CHAT.status = error.message;
    toast(error.message, true);
  } finally {
    for (const message of CHAT.messages) message.streaming = false;
    CHAT.typing = false;
    CHAT.streaming = false;
    if (!AGENT.error) CHAT.status = "";
    setAgentRunning(false);
    renderChat();
    renderAgent();
  }
}

async function regenerateChat() {
  if (!CHAT.conversationId) { toast("send a message before regenerating", true); return; }
  return sendChat("", { regenerate: true });
}

function startEditMessage(index) {
  const message = CHAT.messages[index];
  if (!message || message.role !== "user") return;
  CHAT.editId = message.id || "";
  if (!CHAT.editId) return;
  const input = $("#prompt");
  if (input) {
    input.value = message.text || message.lead || "";
    input.focus?.();
  }
  toast("editing this message — send to replace the turn");
}

async function copyMessage(index) {
  const message = CHAT.messages[index];
  if (!message) return;
  const text = message.text || message.lead || "";
  try {
    if (window.navigator?.clipboard?.writeText) {
      await window.navigator.clipboard.writeText(text);
    } else if (document.createElement) {
      const area = document.createElement("textarea");
      area.value = text;
      document.body?.appendChild?.(area);
      area.select?.();
      document.execCommand?.("copy");
      area.remove?.();
    }
    toast("copied");
  } catch {
    toast("could not copy", true);
  }
}

async function deleteChatMessage(index) {
  const message = CHAT.messages[index];
  if (!message) return;
  if (CHAT.conversationId && message.id) {
    try {
      const data = await api(`/api/conversations/${encodeURIComponent(CHAT.conversationId)}` +
        `/messages/${encodeURIComponent(message.id)}/delete`, {});
      CHAT.messages = messagesFromRows(data.messages);
      renderChat();
      loadConversations($("#conversationSearch")?.value || "");
      return;
    } catch (error) {
      toast(error.message, true);
      return;
    }
  }
  CHAT.messages.splice(index, 1);
  renderChat();
}

async function stopRun() {
  if (CHAT.streaming) {
    try {
      await api("/api/chat/stop", {
        conversation_id: CHAT.conversationId || "",
        run_id: CHAT.runId || "",
      });
      toast("stop requested; nothing unverified will be committed");
    } catch (error) {
      toast(error.message, true);
    }
    return;
  }
  return stopAgent();
}

/* ------------------------------------------------------------ attachments */
function renderAttachments() {
  const strip = $("#attachmentStrip");
  if (!strip) return;
  const images = CHAT.attachments || [];
  strip.hidden = !images.length;
  strip.innerHTML = images.map((url, index) => `<figure class="attachment">
    <img src="${esc(url)}" alt="Attached reference">
    <button data-remove-attachment="${index}" title="Remove attachment">×</button>
  </figure>`).join("");
  renderDesignReferences();
}

function attachImages(event) {
  const files = Array.from(event.target?.files || []);
  if (!files.length) return;
  if (typeof FileReader === "undefined") return;
  for (const file of files) {
    if (CHAT.attachments.length >= 4) break;
    if (Number(file.size || 0) > 1_900_000) {
      toast(`${file.name || "image"} is too large (max 1.9 MB)`, true);
      continue;
    }
    const reader = new FileReader();
    reader.onload = () => {
      if (typeof reader.result === "string") {
        CHAT.attachments.push(reader.result);
        renderAttachments();
      }
    };
    reader.onerror = () => toast(`could not read ${file.name || "image"}`, true);
    reader.readAsDataURL(file);
  }
  if (event.target) event.target.value = "";
}

/* --------------------------------------------------------------- settings */
async function toggleSettings() {
  CHAT.settingsOpen = !CHAT.settingsOpen;
  const box = $("#settingsBox");
  if (!box) return;
  box.hidden = !CHAT.settingsOpen;
  if (!CHAT.settingsOpen) return;
  try {
    const data = await api("/api/settings");
    const settings = data.settings || {};
    if ($("#setProvider")) $("#setProvider").value = settings.provider || "";
    if ($("#setUrl")) $("#setUrl").value = settings.url || "";
    if ($("#setModel")) $("#setModel").value = settings.model || "";
    if ($("#setVision")) $("#setVision").value = settings.vision_model || "";
    if ($("#setSearchUrl")) $("#setSearchUrl").value = settings.search_url || "";
    const hint = $("#settingsHint");
    if (hint) {
      const keyReady = Boolean(data.llm?.key_configured || data.session_key);
      if (!data.llm?.configured) {
        hint.textContent = "No model configured — the local CAD engine is active. Use AI provider to add a session key.";
      } else if (!keyReady) {
        hint.textContent = `Endpoint saved (${data.llm.model}) but no key is held for this session. Add one in AI provider, or the local engine stays in charge.`;
      } else {
        hint.textContent = `Model ready: ${data.llm.model}. The key stays in server memory for this browser session.`;
      }
    }
  } catch (error) {
    toast(error.message, true);
  }
}

async function saveSettings() {
  try {
    const settings = {
      provider: $("#setProvider")?.value || "",
      url: $("#setUrl")?.value || "",
      model: $("#setModel")?.value || "",
      vision_model: $("#setVision")?.value || "",
      search_url: $("#setSearchUrl")?.value || "",
    };
    const data = await api("/api/settings", { settings });
    const hint = $("#settingsHint");
    if (hint) {
      const keyReady = Boolean(data.llm?.key_configured || data.session_key);
      hint.textContent = !data.llm?.configured
        ? "Saved. No model endpoint is configured yet."
        : keyReady
          ? `Saved. Model: ${data.llm.model}.`
          : `Saved. Add a key in AI provider to use ${data.llm.model}.`;
    }
    toast("settings saved");
  } catch (error) {
    toast(error.message, true);
  }
}

async function runPrompt(textOverride) {
  const prompt = String(textOverride ?? $("#prompt").value ?? "").trim();
  if (!prompt) { toast("tell the agent what to change first", true); return; }
  if (AGENT.running) { toast("the agent is already running", true); return; }
  const before = STATE && STATE.spec ? cloneSpec(STATE.spec) : null;
  CHAT.messages.push({ role: "user", text: prompt });
  $("#prompt").value = "";
  CHAT.typing = true;
  AGENT.events = [];
  AGENT.result = null;
  AGENT.changes = [];
  AGENT.variants = [];
  AGENT.error = null;
  AGENT.pending = null;
  renderChat();
  renderAgent();
  setAgentRunning(true);
  setLoading(true, "agent inspecting the design");

  try {
    const payload = await streamAgent({
      prompt,
      mode: currentMode(),
      use_llm: Boolean($("#useLlm") && $("#useLlm").checked),
      stream: true,
      component: CRITIC.focus,
    }, handleAgentEvent);
    if (!payload) throw new Error(AGENT.error || "the agent returned no result");
    AGENT.result = payload;
    AGENT.changes = payload.changes || [];
    AGENT.variants = payload.variants || [];
    AGENT.engine = payload.engine || AGENT.engine;
    CHAT.engine = AGENT.engine;
    CHAT.prevSpec = before;
    CHAT.messages.push(assistantFromAgent(payload));
    applyAgentResult(payload);
    const status = payload.status || "complete";
    const applied = Number(payload.applied || 0);
    if (status === "awaiting_approval") {
      toast("changes are ready for your approval");
    } else if (status === "suggested") {
      toast("proposal ready; nothing was applied");
    } else if (applied) {
      toast(`agent applied ${applied} field${applied === 1 ? "" : "s"}`);
    } else {
      toast(`agent finished: ${status.replaceAll("_", " ")}`);
    }
  } catch (error) {
    CHAT.messages.push({
      role: "assistant",
      lead: `The run could not finish: ${error.message}`,
      notes: ["Nothing unverified was committed. Check the execution timeline for the failing step."],
      bad: true,
    });
    AGENT.error = error.message;
    toast(error.message, true);
  } finally {
    CHAT.typing = false;
    setAgentRunning(false);
    renderChat();
    renderAgent();
    setLoading(false);
  }
}

function assistantFromAgent(result) {
  const changes = result.changes || [];
  const applied = Number(result.applied || 0);
  const status = String(result.status || "");
  const notes = [...(result.notes || [])];
  for (const item of result.unresolved || []) {
    if (!notes.includes(`Unresolved: ${item}`)) notes.push(`Unresolved: ${item}`);
  }
  const state = result;
  let lead = String(result.response || result.final_message || "").trim();
  if (!lead) {
    lead = status === "awaiting_approval"
      ? `I prepared ${changes.length} change(s) and stopped for approval.`
      : status === "suggested"
        ? `I prepared ${changes.length} proposal(s) without applying them.`
        : "The agent finished without a written answer.";
  }
  if (result.error) lead = `${lead} ${result.error}`;
  return {
    role: "assistant",
    lead,
    applied,
    engine: result.engine || "rules",
    notes,
    questions: state.questions || [],
    patches: changes.map((change) => ({
      path: change.path, value: change.to, from: change.from,
    })),
    skipped: [],
    undoable: status === "complete" && applied > 0,
  };
}

function parseSseFrame(frame, dispatch) {
  const data = [];
  for (const line of String(frame || "").split(/\r?\n/)) {
    if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
  }
  if (!data.length) return;
  try { dispatch(JSON.parse(data.join("\n"))); } catch { /* partial frame */ }
}

async function streamAgent(body, onEvent) {
  const revision = appliedRevision || Number(STATE?.revision || 0);
  if (revision && body.expected_revision === undefined) {
    body = { ...body, expected_revision: revision };
  }
  const response = await fetch("/api/agent/run", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-WatchCAD-Session": currentSession(),
    },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    let data = {};
    try { data = await response.json(); } catch { /* empty response */ }
    throw new Error(data.error || `HTTP ${response.status}`);
  }
  const type = String(response.headers?.get("content-type") || "");
  let final = null;
  const dispatch = (event) => {
    if (!event || !event.type) return;
    if (event.type === "result") final = event.result || null;
    onEvent(event);
  };
  const canStream = response.body && typeof response.body.getReader === "function"
    && typeof TextDecoder !== "undefined" && type.includes("text/event-stream");
  if (canStream) {
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let split = buffer.indexOf("\n\n");
      while (split >= 0) {
        parseSseFrame(buffer.slice(0, split), dispatch);
        buffer = buffer.slice(split + 2);
        split = buffer.indexOf("\n\n");
      }
    }
    buffer += decoder.decode();
    if (buffer.trim()) parseSseFrame(buffer, dispatch);
  } else if (type.includes("text/event-stream") || !type.includes("application/json")) {
    const text = await response.text();
    for (const frame of String(text).split(/\n\n/)) parseSseFrame(frame, dispatch);
  } else {
    const data = await response.json();
    dispatch({ type: "result", result: data });
    final = data;
  }
  return final;
}

function handleAgentEvent(event) {
  if (event.type === "start") {
    AGENT.runId = event.run_id || AGENT.runId;
    AGENT.sessionId = event.session_id || AGENT.sessionId;
    AGENT.engine = event.mode ? `${event.mode} run` : AGENT.engine;
    renderAgentStatus();
    return;
  }
  if (event.type === "timeline" && event.event) {
    const item = event.event;
    if (!AGENT.events.some((known) => known.seq && known.seq === item.seq)) {
      AGENT.events.push(item);
    }
    renderAgentTimeline();
    renderAgentStatus();
    return;
  }
  if (event.type === "result") {
    AGENT.result = event.result || AGENT.result;
    return;
  }
  if (event.type === "error") {
    AGENT.error = (event.error && (event.error.error || event.error.response)) || "agent error";
  }
}

function applyAgentResult(result) {
  if (result.snapshot) {
    const snapshot = { ...result.snapshot };
    snapshot.session = result.session || snapshot.session;
    applyState(snapshot);
  }
  AGENT.engine = result.engine || AGENT.engine;
  AGENT.pending = result.pending || null;
  if (!AGENT.pending && result.status === "awaiting_approval" && result.pending_token) {
    AGENT.pending = {
      token: result.pending_token,
      request: result.objective || "",
      ops: (result.staged_ops || []).map((op) => ({
        path: op.path, value: op.value,
      })),
    };
  }
  renderAgent();
}

function formatAgentValue(value) {
  if (value && typeof value === "object") {
    try { return JSON.stringify(value); } catch { return String(value); }
  }
  return String(value ?? "—");
}

function renderAgent() {
  renderSessionChip();
  renderAgentStatus();
  renderAgentTimeline();
  renderAgentDiff();
  renderAgentVariants();
  renderApproval();
}

function renderAgentStatus() {
  const node = $("#agentStatus");
  if (!node) return;
  if (AGENT.running) {
    node.hidden = false;
    node.className = "agent-status running";
    node.innerHTML = `<span class="spinner"></span>Agent running · ${esc(currentMode())} mode`;
    return;
  }
  const result = AGENT.result;
  if (!result && !AGENT.error) { node.hidden = true; node.innerHTML = ""; return; }
  node.hidden = false;
  if (AGENT.error) {
    node.className = "agent-status bad";
    node.innerHTML = `Run failed · ${esc(AGENT.error)}`;
    return;
  }
  const status = String(result.status || "complete");
  const after = result.verification_after || {};
  const changes = (result.changes || []).length;
  const kind = status === "complete" ? "ok"
    : status === "awaiting_approval" || status === "suggested" ? "warn" : "bad";
  const bits = [
    `<b>${esc(status.replaceAll("_", " "))}</b>`,
    `${esc(result.mode || currentMode())} mode`,
    `${esc(result.intent || "modify")} intent`,
    changes ? `${changes} change${changes === 1 ? "" : "s"}` : "no changes",
    `${Number(after.fail || 0)} failing`,
  ];
  node.className = `agent-status ${kind}`;
  node.innerHTML = bits.join(`<span class="sep">·</span>`);
}

function renderAgentTimeline() {
  const holder = $("#agentTimeline");
  if (!holder) return;
  const events = AGENT.events || [];
  holder.innerHTML = events.length
    ? events.map((event) => {
        const status = String(event.status || "done");
        const iteration = Number(event.iteration || 0);
        const data = event.data || {};
        const detail = event.detail ? `<p>${esc(event.detail)}</p>` : "";
        let extra = "";
        if (data.arguments && Object.keys(data.arguments).length) {
          extra = `<div class="tl-data">${esc(shortJson(data.arguments))}</div>`;
        } else if (data.path) {
          extra = `<div class="tl-data">${esc(data.path)} = ${esc(formatAgentValue(data.value))}</div>`;
        } else if (data.changes) {
          extra = `<div class="tl-data">${esc(shortJson(data.changes))}</div>`;
        }
        return `<details class="tl ${esc(status)}" ${status === "error" || status === "warning" ? "open" : ""}>
          <summary><span class="tl-dot"></span><b>${esc(event.title || event.kind)}</b>
          <span class="tl-kind">${esc(event.kind || "")}</span>
          ${iteration ? `<em class="tl-iter">iteration ${iteration}</em>` : ""}</summary>
          ${detail}${extra}</details>`;
      }).join("")
    : `<div class="empty-note">No run yet. Ask the agent to design, inspect, explain or repair.</div>`;
  const iterations = Math.max(0, ...events.map((event) => Number(event.iteration || 0)));
  const label = $("#agentIterations");
  if (label) label.textContent = iterations ? `${iterations} iteration${iterations === 1 ? "" : "s"}` : "";
}

function shortJson(value, limit = 240) {
  let text;
  try { text = JSON.stringify(value); } catch { text = String(value); }
  text = String(text ?? "");
  return text.length > limit ? `${text.slice(0, limit)}…` : text;
}

function renderAgentDiff() {
  const holder = $("#agentDiff");
  if (!holder) return;
  const changes = AGENT.changes || [];
  const count = $("#agentChangeCount");
  if (count) count.textContent = changes.length ? String(changes.length) : "";
  holder.innerHTML = changes.length
    ? changes.map((change) => `<div class="diff-row">
        <span class="diff-path">${esc(change.path)}</span>
        <span class="diff-from">${esc(formatAgentValue(change.from))}</span>
        <span class="diff-arrow">→</span>
        <span class="diff-to">${esc(formatAgentValue(change.to))}</span>
      </div>`).join("")
    : `<div class="empty-note">No verified change in this run.</div>`;
}

function renderAgentVariants() {
  renderDesignVariations();
  const box = $("#agentVariantsBox");
  const holder = $("#agentVariants");
  if (!box || !holder) return;
  const variants = AGENT.variants || [];
  box.hidden = !variants.length;
  const count = $("#agentVariantCount");
  if (count) count.textContent = variants.length ? String(variants.length) : "";
  holder.innerHTML = variants.map((variant, index) => {
    const changes = variant.changes || [];
    const after = variant.verification || {};
    return `<div class="variant">
      <div class="variant-head"><b>${esc(variant.label || `Alternative ${index + 1}`)}</b>
        <button class="btn small" data-variant="${index}">Apply</button></div>
      <p>${esc(variant.rationale || "")}</p>
      <div class="chips">${changes.map((change) =>
        `<span>${esc(change.path)} → ${esc(formatAgentValue(change.to))}</span>`).join("")}</div>
      <div class="variant-meta">${Number(after.fail || 0)} failing · checksum ${esc(String(variant.checksum || "").slice(0, 8))}</div>
    </div>`;
  }).join("");
}

function renderApproval() {
  const holder = $("#agentApproval");
  if (!holder) return;
  const pending = AGENT.pending;
  if (!pending || !pending.token) { holder.hidden = true; holder.innerHTML = ""; return; }
  const ops = pending.ops || [];
  holder.hidden = false;
  holder.innerHTML = `<div class="approval-head">Approval required</div>
    <p>${esc(pending.request || "The agent prepared a change for this design.")}</p>
    <div class="agent-diff">${ops.map((op) => `<div class="diff-row">
      <span class="diff-path">${esc(op.path)}</span>
      <span class="diff-arrow">→</span>
      <span class="diff-to">${esc(formatAgentValue(op.value))}</span>
    </div>`).join("")}</div>
    <div class="approval-actions">
      <button class="btn primary small" data-approve="1">Approve and apply</button>
      <button class="btn small" data-reject="1">Reject</button>
    </div>`;
}

async function approveAgent(approve) {
  const token = AGENT.pending && AGENT.pending.token;
  if (!token) { toast("nothing is waiting for approval", true); return; }
  setLoading(true, approve ? "applying the approved change" : "rejecting the change");
  try {
    if (CHAT.pending?.token === token && CHAT.conversationId) {
      const data = await api(approve ? "/api/chat/approve" : "/api/chat/reject", {
        conversation_id: CHAT.conversationId,
        token,
      });
      CHAT.pending = null;
      AGENT.pending = null;
      CHAT.messages = messagesFromRows(data.messages);
      if (data.session?.session_id) rememberSession(data.session.session_id);
      if (data.snapshot) applyState(data.snapshot);
      renderChat();
      renderAgent();
      toast(approve ? "approved and applied" : "change rejected");
      return;
    }
    const state = await api(approve ? "/api/agent/approve" : "/api/agent/reject", { token });
    AGENT.pending = null;
    AGENT.changes = approve
      ? (state.changes || []).map((op) => ({ path: op.path, to: op.value, from: op.from }))
      : [];
    CHAT.messages.push({
      role: "assistant",
      lead: approve
        ? "Approved and applied. The deterministic solver and verifier ran on the staged spec."
        : "Rejected. The live design was not changed.",
    });
    applyState(state);
    renderChat();
    renderAgent();
    toast(approve ? "approved and applied" : "change rejected");
  } catch (error) {
    toast(error.message, true);
  } finally {
    setLoading(false);
  }
}

async function applyVariant(index) {
  const variant = (AGENT.variants || [])[Number(index)];
  if (!variant) { toast("that alternative is no longer available", true); return; }
  setAgentRunning(true);
  setLoading(true, `applying “${variant.label || "alternative"}”`);
  try {
    const result = await api("/api/agent/variant", {
      variant,
      mode: currentMode(),
    });
    AGENT.result = result;
    AGENT.changes = result.changes || [];
    AGENT.variants = result.variants || AGENT.variants;
    AGENT.pending = result.pending || null;
    CHAT.engine = result.engine || CHAT.engine;
    CHAT.messages.push(assistantFromAgent(result));
    applyAgentResult(result);
    renderChat();
    toast(result.status === "awaiting_approval"
      ? "alternative staged for approval"
      : "alternative applied through the validated tool pipeline");
  } catch (error) {
    toast(error.message, true);
  } finally {
    setAgentRunning(false);
    setLoading(false);
  }
}

async function stopAgent() {
  if (!AGENT.running) return;
  try {
    await api("/api/agent/cancel", { run_id: AGENT.runId || "" });
    toast("stop requested; no unverified change will be committed");
  } catch (error) {
    toast(error.message, true);
  }
}

async function toggleAgentHistory() {
  const box = $("#agentHistory");
  if (!box) return;
  if (!box.hidden) { box.hidden = true; return; }
  box.hidden = false;
  box.innerHTML = `<div class="empty-note">loading history…</div>`;
  try {
    AGENT.history = await api("/api/agent/history");
    renderAgentHistory();
  } catch (error) {
    box.innerHTML = `<div class="empty-note bad">${esc(error.message)}</div>`;
  }
}

function renderAgentHistory() {
  const box = $("#agentHistory");
  if (!box || !AGENT.history) return;
  const data = AGENT.history;
  const versions = data.versions || data.history || [];
  const memory = data.memory || {};
  const rows = versions.length ? versions.slice().reverse().map((version) =>
    `<div class="history-row">
      <span class="history-index">v${esc(version.index)}</span>
      <span class="history-label">${esc(version.label || "")}</span>
      <span class="history-kind">${esc(version.kind || "")}</span>
      <span class="history-check">${esc(String(version.checksum || "").slice(0, 8))}</span>
    </div>`).join("") : `<div class="empty-note">No versions yet.</div>`;
  const constraints = (memory.constraints || []).slice(-5);
  box.innerHTML = `<div class="history-head">Design history
      <button class="btn small" data-history-close="1">Close</button></div>
    ${rows}
    <div class="history-memory">
      <div><b>Intent</b> ${esc(memory.intent || "—")}</div>
      <div><b>Locks</b> ${esc((memory.locked_fields || []).join(", ") || "none")}</div>
      <div><b>Constraints</b> ${esc(constraints.join("; ") || "none")}</div>
    </div>`;
}

async function undoTurn() {
  if (AGENT.running) { toast("stop the agent first", true); return; }
  setLoading(true, "restoring the previous version");
  try {
    let state;
    try {
      state = await api("/api/agent/undo", {});
    } catch (error) {
      if (!CHAT.prevSpec) throw error;
      state = await api("/api/spec", { spec: CHAT.prevSpec });
    }
    CHAT.prevSpec = null;
    for (const message of CHAT.messages) message.undoable = false;
    AGENT.changes = [];
    AGENT.result = null;
    AGENT.variants = [];
    AGENT.pending = null;
    CHAT.messages.push({ role: "assistant", lead: "Reverted. We’re back to the previous design version." });
    applyState(state);
    renderChat();
    renderAgent();
    toast("reverted the last design version");
  } catch (error) {
    toast(error.message, true);
  } finally {
    setLoading(false);
  }
}

async function redoTurn() {
  if (AGENT.running) { toast("stop the agent first", true); return; }
  setLoading(true, "restoring the next version");
  try {
    const state = await api("/api/agent/redo", {});
    AGENT.changes = [];
    AGENT.result = null;
    CHAT.messages.push({ role: "assistant", lead: "Restored the next design version." });
    applyState(state);
    renderChat();
    renderAgent();
    toast("restored the next version");
  } catch (error) {
    toast(error.message, true);
  } finally {
    setLoading(false);
  }
}

/* Legacy alias used by the smoke harness and by older chat links. */
async function undoPrompt() {
  return undoTurn();
}

function renderChat() {
  const log = $("#chatLog");
  if (!log) return;
  log.innerHTML = CHAT.messages.map((message, index) => messageHTML(message, index)).join("");
  log.scrollTop = log.scrollHeight;
  updateEngineChip();
  renderChatStatus();
}

function messageActions(message, index) {
  if (message.streaming) return "";
  const actions = [];
  const text = message.text || message.lead || "";
  if (text) actions.push(`<button data-copy-msg="${index}" title="Copy message">Copy</button>`);
  if (message.role === "user" && message.id) {
    actions.push(`<button data-edit-msg="${index}" title="Edit this message">Edit</button>`);
  }
  if (message.role === "assistant" && index === CHAT.messages.length - 1) {
    actions.push(`<button data-regenerate-msg="${index}" title="Regenerate this answer">Regenerate</button>`);
  }
  if (message.id) {
    actions.push(`<button data-delete-msg="${index}" title="Delete message">Delete</button>`);
  }
  return actions.length ? `<div class="msg-actions">${actions.join("")}</div>` : "";
}

function attachmentHTML(urls) {
  if (!urls || !urls.length) return "";
  return `<div class="msg-images">${urls.map((url) =>
    `<img src="${esc(url)}" alt="Attached reference">`).join("")}</div>`;
}

function messageHTML(message, index = 0) {
  const actions = messageActions(message, index);
  if (message.role === "user") {
    return `<div class="msg user">
      <span class="avatar">Y</span>
      <div class="bubble">${attachmentHTML(message.attachments)}
        <div class="lead user-text">${renderMarkdown(message.text || message.lead || "")}</div>
        ${actions}</div></div>`;
  }
  const body = [];
  if (message.lead) {
    body.push(`<div class="lead ${message.bad ? "bad" : ""}">${renderMarkdown(message.lead)}${
      message.streaming ? '<span class="stream-cursor"></span>' : ""}</div>`);
  } else if (message.streaming) {
    body.push(`<div class="typing"><span></span><span></span><span></span></div>`);
  }
  for (const note of message.notes || []) {
    body.push(`<div class="note">${esc(note)}</div>`);
  }
  if (message.status && message.bad) {
    body.push(`<div class="msg-status bad">${esc(String(message.status).replaceAll("_", " "))}</div>`);
  }
  if (message.patches && message.patches.length) {
    body.push(`<div class="chips">${message.patches.slice(0, 10).map((patch) => {
      let value = patch.value;
      if (value && typeof value === "object") value = JSON.stringify(value);
      return `<span>${esc(patch.path)} → ${esc(String(value ?? "").slice(0, 38))}</span>`;
    }).join("")}${message.patches.length > 10 ? `<span>+${message.patches.length - 10} more</span>` : ""}</div>`);
  }
  if (message.questions && message.questions.length) {
    body.push(`<div class="q-title">One thing I need</div>`);
    body.push(`<ul>${message.questions.map((question) => `<li>${esc(question)}</li>`).join("")}</ul>`);
  }
  if (message.suggestions && message.suggestions.length) {
    body.push(`<div class="chips">${message.suggestions.map((suggestion) =>
      `<button data-suggest="${esc(suggestion)}">${esc(suggestion)}</button>`).join("")}</div>`);
  }
  if (message.undoable) {
    body.push(`<button class="undo" data-undo="1">Undo those changes</button>`);
  }
  if (actions) body.push(actions);
  return `<div class="msg assistant"><span class="avatar">W</span>
    <div class="bubble">${body.join("")}</div></div>`;
}

function renderChatStatus() {
  const node = $("#chatStatus");
  if (!node) return;
  const running = CHAT.streaming || CHAT.typing;
  if (running) {
    node.hidden = false;
    node.className = "chat-status running";
    node.innerHTML = `<span class="spinner small"></span>${esc(CHAT.status || "Thinking")}`;
    return;
  }
  if (CHAT.status && AGENT.error) {
    node.hidden = false;
    node.className = "chat-status bad";
    node.textContent = CHAT.status;
    return;
  }
  node.hidden = true;
  node.className = "chat-status";
  node.innerHTML = "";
}

function updateEngineChip() {
  const chip = $("#engineChip");
  if (!chip) return;
  const engine = CHAT.engine || "rules";
  chip.textContent = engine.includes("llm") ? "rules + llm" : "local rules";
  chip.classList.toggle("llm", engine.includes("llm"));
}

/* ----------------------------------------------------------------- spec */
function renderSpec() {
  if (!STATE || !SCHEMA) return;
  const spec = STATE.spec.data;
  const provenance = STATE.spec.provenance || {};
  const locks = new Set(STATE.spec.locks || []);
  const groups = [
    ["case", "Case", true],
    ["dial", "Dial", true],
    ["hands", "Hands", true],
    ["movement", "Movement & complications", true],
    ["quartz", "Quartz module", false],
    ["meta", "Project", false],
  ];
  const html = [];
  for (const [group, label, open] of groups) {
    const fields = SCHEMA.groups[group] || [];
    if (!fields.length) continue;
    html.push(`<details class="group" ${open ? "open" : ""}><summary>${esc(label)}<span class="count">${fields.length}</span></summary><div class="group-body">`);
    for (const field of fields) {
      html.push(renderField(field, spec, provenance, locks));
    }
    html.push(`</div></details>`);
  }
  html.push(renderDerived(spec));
  $("#specForm").innerHTML = html.join("");
}

function renderField(field, spec, provenance, locks) {
  const path = field.path;
  const value = getPath(spec, path);
  const tag = (provenance[path] && provenance[path].tag) || "default";
  const note = provenance[path]?.note || provenance[path]?.source || "";
  const locked = locks.has(path);
  const vanilla = ["meta.version", "meta.created_at"].includes(path);
  const isComp = path === "movement.complications";

  const name = path.split(".").slice(-1)[0].replaceAll("_", " ");
  const unit = field.unit ? `<span class="unit">${esc(field.unit)}</span>` : "";
  const prov = `<span class="prov ${esc(tag)}" title="${esc(note || "no provenance note")}">${esc(tag)}</span>`;
  const lock = vanilla ? "" :
    `<button class="lockbtn ${locked ? "locked" : ""}" data-lock="${esc(path)}"
       title="${locked ? "unlock this field" : "lock this field against prompt patches"}">${locked ? "locked" : "lock"}</button>`;
  let control;
  if (isComp) {
    control = renderComplications(value || {});
  } else if (vanilla) {
    control = `<span class="ro">${esc(value ?? "")}</span>`;
  } else if (field.kind === "bool") {
    control = `<input type="checkbox" data-kind="bool" ${value ? "checked" : ""}>`;
  } else if (field.kind === "enum") {
    const choices = field.choices || [];
    const opts = choices.map((choice) =>
      `<option value="${esc(choice)}" ${choice === value ? "selected" : ""}>${esc(choice)}</option>`);
    if (!choices.includes(value)) {
      opts.unshift(`<option value="${esc(value)}" selected>${esc(value)}</option>`);
    }
    control = `<select data-kind="enum">${opts.join("")}</select>`;
  } else if (field.kind === "num" || field.kind === "int") {
    const span = field.max !== undefined && field.min !== undefined
      ? Math.abs(field.max - field.min) : 0;
    let step;
    if (field.kind === "int") step = span >= 10000 ? 500 : span >= 500 ? 10 : 1;
    else step = span >= 100 ? 1 : span >= 20 ? 0.5 : 0.1;
    const bounds = `${field.min !== undefined ? ` min="${field.min}"` : ""}${field.max !== undefined ? ` max="${field.max}"` : ""}`;
    control = `<div class="number-control">
      <input type="number" class="num" data-kind="${field.kind}" data-number
        step="${step}" value="${Number(value)}"${bounds}>
      <span class="stepper">
        <button type="button" class="step" data-step="1" title="increase" aria-label="increase ${esc(name)}">▲</button>
        <button type="button" class="step" data-step="-1" title="decrease" aria-label="decrease ${esc(name)}">▼</button>
      </span>
    </div>`;
  } else if (field.kind === "dict") {
    control = `<span class="ro">${esc(JSON.stringify(value || {}).slice(0, 80))}</span>`;
  } else {
    const long = path === "meta.notes";
    control = long
      ? `<input type="text" data-kind="str" value="${esc(value ?? "")}">`
      : `<input type="text" data-kind="str" value="${esc(value ?? "")}">`;
  }

  return `<div class="field ${locked ? "locked" : ""} ${vanilla ? "disabled" : ""}"
      data-path="${esc(path)}" data-kind="${esc(field.kind)}">
    <div class="field-head">
      <span class="field-name">${esc(name)} ${unit}</span>
      ${prov}${lock}
    </div>
    <div class="control">${control}</div>
  </div>`;
}

function renderComplications(selected) {
  const modules = SCHEMA.complications || [];
  const cells = modules.map((module) => {
    const on = Object.prototype.hasOwnProperty.call(selected, module.id);
    return `<label class="comp ${on ? "on" : ""}" title="${esc(module.description)}">
      <input type="checkbox" data-comp="${esc(module.id)}" ${on ? "checked" : ""}>
      <span>${esc(module.name)}</span>
      <small>${fmt(module.height_mm, 1)} mm</small>
    </label>`;
  });
  return `<div class="comp-grid">${cells.join("")}</div>`;
}

function renderDerived(spec) {
  const derived = spec.derived || {};
  const stack = derived.stack || {};
  const train = derived.train || {};
  const barrel = derived.barrel || {};
  const torque = derived.torque || {};
  const quartz = derived.quartz_train || {};
  const rows = [];
  if (derived.checksum) rows.push(["checksum", derived.checksum]);
  if (spec.movement.kind === "quartz") {
    rows.push(["battery life", `${fmt(derived.battery_life_years, 1)} years`]);
    rows.push(["pulses/min", `${fmt(quartz.pulses_per_minute, 1)}`]);
    rows.push(["reduction", `${fmt(quartz.seconds_reduction, 2)}:1`]);
  } else {
    rows.push(["stack required", `${fmt(stack.required_case_thickness_mm, 2)} mm`]);
    rows.push(["stack available", `${fmt(stack.available_case_thickness_mm, 2)} mm`]);
    rows.push(["margin", `${fmt(stack.margin_mm, 2)} mm`]);
    rows.push(["meshes", `${(train.stages || []).length}`]);
    rows.push(["barrel→escape", `${fmt(train.total_barrel_to_escape, 1)}:1`]);
    rows.push(["reserve effective", `${fmt(train.effective_power_reserve_h, 1)} h`]);
    rows.push(["barrel bore", `${fmt(barrel.inner_diameter_mm, 2)} mm`]);
    rows.push(["spring", `${fmt(barrel.spring_thickness_mm, 3)} × ${fmt(barrel.spring_length_mm, 0)} mm`]);
    rows.push(["power margin", `${fmt(torque.power_margin, 2)}×`]);
    rows.push(["escape margin", `${fmt(torque.escape_margin, 2)}×`]);
  }
  if (!rows.length) return "";
  return `<details class="group"><summary>Derived — read only<span class="count">${rows.length}</span></summary>
    <div class="group-body"><table class="derived-table">${rows.map(([key, value]) =>
      `<tr><td>${esc(key)}</td><td>${esc(value)}</td></tr>`).join("")}</table></div></details>`;
}

/* --------------------------------------------------------------- fields */
function onFieldStep(event) {
  const button = event.target.closest("[data-step]");
  if (!button) return;
  const row = button.closest(".field");
  const input = row && row.querySelector("[data-number]");
  if (!row || !input) return;
  const step = Math.abs(Number(input.step)) || (row.dataset.kind === "int" ? 1 : 0.1);
  let next = (Number(input.value) || 0) + Number(button.dataset.step) * step;
  if (input.min !== "") next = Math.max(Number(input.min), next);
  if (input.max !== "") next = Math.min(Number(input.max), next);
  if (row.dataset.kind === "int") next = Math.round(next);
  next = Number(next.toFixed(6));
  input.value = String(next);
  setField(row.dataset.path, next);
}

function onFieldChange(event) {
  const row = event.target.closest(".field");
  if (!row) return;
  const path = row.dataset.path;
  if (event.target.matches("[data-comp]")) {
    const selected = {};
    for (const box of $$("[data-comp]", row)) {
      if (box.checked) selected[box.dataset.comp] = {};
    }
    setField(path, selected);
    row.querySelectorAll(".comp").forEach((label) => {
      label.classList.toggle("on", label.querySelector("input").checked);
    });
    return;
  }
  const kind = row.dataset.kind;
  let value;
  if (kind === "bool") value = event.target.checked;
  else if (kind === "num" || kind === "int") {
    value = Number(event.target.value);
    if (!Number.isFinite(value)) { syncFormValues(); return; }
    if (kind === "int") value = Math.round(value);
    if (event.target.min !== "") value = Math.max(Number(event.target.min), value);
    if (event.target.max !== "") value = Math.min(Number(event.target.max), value);
    event.target.value = String(value);
  } else value = event.target.value;
  setField(path, value);
}

async function setField(path, value, force = false) {
  try {
    const state = await api("/api/set", { path, value, force });
    applyState(state, { spec: false });
    if (value && typeof value === "object") toast(`${path} updated`);
    else toast(`${path} = ${value}`);
  } catch (error) {
    toast(error.message, true);
    syncFormValues();
  }
}

function setComponents(payload) {
  TREE.components = Array.isArray(payload?.components) ? payload.components : [];
  TREE.checkOwners = new Map();
  for (const component of TREE.components) {
    for (const check of component.checks || []) TREE.checkOwners.set(check, component);
  }
  TREE.error = "";
  TREE.loading = false;
  renderComponentTree();
}

function checksForComponent(component) {
  const ids = new Set(component?.checks || []);
  return (STATE?.checks || []).filter((check) => ids.has(check.id));
}

function componentDomId(id) {
  return `tree-component-${String(id).replace(/[^a-zA-Z0-9_-]/g, "-")}`;
}

function treeMatches(component) {
  if (!TREE.query) return true;
  const query = TREE.query;
  const haystack = [
    component.id, component.label, component.group, component.description,
    ...(component.aliases || []), ...(component.parts || []),
  ].join(" ").toLowerCase();
  return haystack.includes(query);
}

function setTreeExpanded(expand) {
  if (expand) TREE.expanded = new Set(TREE.components.map((component) => component.id));
  else TREE.expanded = new Set();
  renderComponentTree();
}

function setComponentVisibility(component, visible) {
  const parts = partsForComponent(component);
  for (const part of parts) {
    if (visible) V.hiddenParts.delete(part);
    else V.hiddenParts.add(part);
  }
  V.isolateParts = null;
  emitVisibilityState();
  renderComponentTree();
  renderGroupToggles(V.tree);
  requestRender();
}

function isolateComponent(component) {
  const parts = partsForComponent(component);
  if (V.isolateParts && V.isolateParts.size === parts.length
    && parts.every((part) => V.isolateParts.has(part))) {
    V.isolateParts = null;
  } else {
    V.isolateParts = new Set(parts);
  }
  emitVisibilityState();
  renderComponentTree();
  renderGroupToggles(V.tree);
  requestRender();
}

function lockLabel(component) {
  const locks = new Set(STATE?.spec?.locks || []);
  const fields = component.dimension_fields || [];
  const locked = fields.filter((path) => locks.has(path)).length;
  if (!fields.length) return "";
  return locked ? `${locked}/${fields.length} locked` : "";
}

function renderComponentTree() {
  const holder = $("#componentTree");
  if (!holder) return;
  if (TREE.loading) {
    holder.innerHTML = `<div class="empty-note">Loading the component registry…</div>`;
    return;
  }
  if (!TREE.components.length) {
    holder.innerHTML = `<div class="empty-note">No component registry available.</div>`;
    return;
  }
  const groups = new Map();
  for (const component of TREE.components) {
    if (!treeMatches(component)) continue;
    const group = component.group || "Other";
    if (!groups.has(group)) groups.set(group, []);
    groups.get(group).push(component);
  }
  if (!groups.size) {
    holder.innerHTML = `<div class="empty-note">No components match “${esc(TREE.query)}”.</div>`;
    return;
  }
  let html = "";
  for (const [group, components] of groups.entries()) {
    html += `<div class="tree-group"><div class="tree-row group-row">
      <span class="tree-label">${esc(group)}</span>
      <span class="tree-count">${components.length}</span>
    </div>`;
    for (const component of components) {
      const selected = V.selectedComponent === component.id || CRITIC.focus?.name === component.id;
      const parts = partsForComponent(component);
      const hidden = parts.length && parts.every((part) => V.hiddenParts.has(part));
      const partlyHidden = parts.length && parts.some((part) => V.hiddenParts.has(part)) && !hidden;
      const isolated = V.isolateParts && parts.length
        && parts.every((part) => V.isolateParts.has(part));
      const checks = checksForComponent(component);
      const failed = checks.filter((check) => check.passed === false).length;
      const expanded = TREE.expanded.has(component.id) || Boolean(TREE.query);
      const state = hidden ? "off" : partlyHidden || (V.isolateParts && !isolated) ? "dim" : "";
      const lockText = lockLabel(component);
      const lockFields = component.dimension_fields?.length
        ? component.dimension_fields : component.fields || [];
      const lockButton = lockFields.length
        ? `<button class="tree-action tree-lock ${lockText.startsWith(lockFields.length) ? "active" : ""}" data-tree-lock="${esc(component.id)}" title="${lockText || `Lock ${component.label} dimensions`}" aria-label="${lockText || `Lock ${component.label} dimensions`}">🔒</button>`
        : "";
      html += `<div class="tree-row component-row ${selected ? "selected" : ""} ${state}">
        <button class="tree-toggle" data-tree-expand="${esc(component.id)}" aria-label="${expanded ? "Collapse" : "Expand"} ${esc(component.label)}">${expanded ? "−" : "+"}</button>
        <button class="tree-action ${hidden ? "" : "active"}" data-tree-visibility="${esc(component.id)}" title="${hidden ? "Show component" : "Hide component"}" aria-label="${hidden ? "Show" : "Hide"} ${esc(component.label)}">●</button>
        <button class="tree-action ${isolated ? "active" : ""}" data-tree-isolate="${esc(component.id)}" title="Isolate this component" aria-label="Isolate ${esc(component.label)}">◎</button>
        <button class="tree-label" data-tree-select="${esc(component.id)}" id="${componentDomId(component.id)}">${esc(component.label)}</button>
        ${failed ? `<span class="tree-count" title="${failed} failing check(s)">${failed}!</span>` : ""}
        ${lockButton}
      </div>`;
      if (expanded) {
        for (const part of parts) {
          const partHidden = V.hiddenParts.has(part);
          const partIsolated = V.isolateParts && V.isolateParts.has(part);
          html += `<div class="tree-row part-row ${partHidden ? "off" : (V.isolateParts && !partIsolated ? "dim" : "")}">
            <span class="tree-toggle" aria-hidden="true">•</span>
            <button class="tree-action ${partHidden ? "" : "active"}" data-tree-part-visibility="${esc(part)}" title="${partHidden ? "Show part" : "Hide part"}">●</button>
            <button class="tree-label" data-tree-part="${esc(part)}" title="Select ${esc(part)}">${esc(part)}</button>
          </div>`;
        }
      }
    }
    html += `</div>`;
  }
  holder.innerHTML = html;
  const status = $("#componentTreeStatus");
  if (status) {
    if (TREE.query) status.textContent = `${groups.size} group(s) match “${TREE.query}”.`;
    else status.textContent = "Select a component to focus the assistant and highlight its parts.";
  }
}

function selectComponent(id) {
  const component = componentById(id);
  if (!component) return;
  TREE.selected = id;
  const parts = partsForComponent(component);
  for (const part of parts) V.hiddenParts.delete(part);
  emitVisibilityState();
  setComponentFocus({ id: component.id, name: component.label, component: component.id });
  renderComponentTree();
  requestRender();
}

function selectPartByName(name) {
  const index = (V.mesh?.ranges || []).findIndex((range) => range.name === name);
  const range = index >= 0 ? V.mesh.ranges[index] : null;
  if (range && !isRangeVisible(range)) return;
  if (index >= 0) {
    V.selected = index;
    V.hover = index;
    updatePartHud();
    updateViewerCursor();
    viewerEvent("watchcad:select-part", {
      name: range.name, role: range.role, group: range.group,
    });
  } else {
    setComponentFocus({ name });
  }
  requestRender(false);
}

async function toggleComponentLock(id) {
  const component = componentById(id);
  const fields = component?.dimension_fields?.length
    ? component.dimension_fields : component?.fields || [];
  if (!component || !fields.length) {
    toast("this component has no writable dimension to lock", true);
    return;
  }
  const locks = new Set(STATE?.spec?.locks || []);
  const target = !fields.every((path) => locks.has(path));
  try {
    for (const path of fields) {
      const state = await api("/api/lock", { path, locked: target });
      applyState(state, { scene: false });
    }
    toast(`${component.label}: ${target ? "locked" : "unlocked"} ${fields.length} field(s)`);
  } catch (error) {
    toast(error.message, true);
  }
}

function syncFormValues() {
  if (!STATE) return;
  const spec = STATE.spec.data;
  const locks = new Set(STATE.spec.locks || []);
  for (const row of $$("#specForm .field")) {
    const path = row.dataset.path;
    const kind = row.dataset.kind;
    const value = getPath(spec, path);
    row.classList.toggle("locked", locks.has(path));
    const lockButton = row.querySelector("[data-lock]");
    if (lockButton) {
      lockButton.classList.toggle("locked", locks.has(path));
      lockButton.textContent = locks.has(path) ? "locked" : "lock";
    }
    if (row.contains(document.activeElement)) continue;
    if (path === "movement.complications") {
      for (const box of $$("[data-comp]", row)) {
        const on = Boolean(value && Object.prototype.hasOwnProperty.call(value, box.dataset.comp));
        box.checked = on;
        box.closest(".comp").classList.toggle("on", on);
      }
      continue;
    }
    if (kind === "bool") {
      const box = row.querySelector("input[type=checkbox]");
      if (box) box.checked = Boolean(value);
    } else if (kind === "enum") {
      const select = row.querySelector("select");
      if (select) select.value = value;
    } else if (kind === "num" || kind === "int") {
      const number = row.querySelector("[data-number]");
      if (number) number.value = value;
    } else if (kind === "str") {
      const input = row.querySelector("input");
      if (input) input.value = value ?? "";
    }
  }
}

async function toggleLock(path) {
  const locks = new Set(STATE.spec.locks || []);
  try {
    const state = await api("/api/lock", { path, locked: !locks.has(path) });
    applyState(state);
    toast(locks.has(path) ? `${path} unlocked` : `${path} locked; prompt patches will respect it`);
  } catch (error) {
    toast(error.message, true);
  }
}

/* --------------------------------------------------------------- checks */
function renderChecks() {
  if (!STATE) return;
  const checks = STATE.checks || [];
  const summary = STATE.summary || {};
  const chips = [
    `<span class="chip ${summary.fail ? "bad" : "good"}"><b>${summary.fail || 0}</b> failed</span>`,
    `<span class="chip info"><b>${summary.calculated || 0}</b> calculated</span>`,
    `<span class="chip warn"><b>${summary.estimated || 0}</b> estimated</span>`,
    `<span class="chip"><b>${summary.needs_test || 0}</b> needs test</span>`,
  ];
  $("#checkSummary").innerHTML = chips.join("");
  $("#checkCount").textContent = summary.fail ? `${summary.fail}!` : `${checks.length}`;

  const layers = ["all", ...new Set(checks.map((check) => check.layer))];
  $("#checkFilters").innerHTML = layers.map((layer) => {
    const count = layer === "all" ? checks.length : checks.filter((c) => c.layer === layer).length;
    return `<button data-filter="${esc(layer)}" class="${checkFilter === layer ? "active" : ""}">${esc(layer)} ${count}</button>`;
  }).join("");

  const visible = checks.filter((check) => checkFilter === "all" || check.layer === checkFilter);
  $("#checkList").innerHTML = visible.map((check) => {
    const verdict = check.passed === false ? "fail" : check.verdict;
    const index = checks.indexOf(check);
    const fix = check.fix && check.fix.path
      ? `<button class="btn small" data-fix="${index}">Apply fix</button>`
      : (check.fix && check.fix.message
        ? `<button class="btn small" data-message="${index}">What to change</button>` : "");
    return `<div class="check ${esc(verdict)}">
      <div class="check-head">
        <span class="check-title">${esc(check.title)}</span>
        <span class="verdict">${esc(verdict)}</span>
      </div>
      <div class="check-detail">${esc(check.detail)}</div>
      <div class="check-foot"><span class="check-id">${esc(check.id)}</span>${fix}</div>
    </div>`;
  }).join("") || `<div class="empty-note">No checks in this layer.</div>`;
}

async function applyFix(index) {
  const check = (STATE.checks || [])[Number(index)];
  if (!check || !check.fix || !check.fix.path) return;
  toast(`setting ${check.fix.path} = ${check.fix.value}`);
  await setField(check.fix.path, check.fix.value);
}

/* ------------------------------------------------------------ bottom dock */
function verdictParts(check) {
  if (check.passed === false) return { label: "FAIL", tone: "fail", confidence: "failed" };
  if (check.verdict === "estimated") return { label: "PASS", tone: "warning", confidence: "estimated" };
  if (check.verdict === "needs_test") return { label: "NEEDS TEST", tone: "warning", confidence: "needs test" };
  return { label: "PASS", tone: "pass", confidence: check.verdict || "calculated" };
}

function renderVerificationDock() {
  const holder = $("#verificationDock");
  if (!holder || !STATE) return;
  const checks = STATE.checks || [];
  const summary = STATE.summary || {};
  const failing = checks.filter((check) => check.passed === false);
  const chips = `<span class="chip ${failing.length ? "bad" : "good"}"><b>${failing.length}</b> failing</span>
    <span class="chip warn"><b>${summary.needs_test || 0}</b> need bench test</span>
    <span class="chip info"><b>${summary.estimated || 0}</b> estimated</span>`;
  const rows = checks.map((check, index) => {
    const verdict = verdictParts(check);
    const owner = TREE.checkOwners.get(check.id)?.label || check.layer || "Whole design";
    const action = check.fix?.path
      ? `<button class="btn small" data-fix="${index}">Apply corrective action</button>`
      : check.fix?.message
        ? `<button class="btn small" data-message="${index}">Show guidance</button>`
        : "No automatic correction";
    return `<article class="verification-row ${verdict.tone}">
      <div class="verification-head"><b>${esc(check.title || check.id)}</b>
        <span class="verdict-label ${verdict.tone}">${esc(verdict.label)}</span></div>
      <div class="verification-fields">
        <span>Confidence</span><b>${esc(verdict.confidence)}</b>
        <span>Component</span><b>${esc(owner)}</b>
        <span>Reported</span><b>${esc(check.detail || "—")}</b>
        <span>Constraint</span><b>${esc(check.fix?.message || "No automatic fix; see the calculation basis")}</b>
        <span>Basis</span><b>${esc(check.layer || "spec")} · ${esc(check.id)} · ${esc(check.verdict || "calculated")}</b>
        <span>Corrective action</span><b>${action}</b>
      </div>
    </article>`;
  }).join("");
  holder.innerHTML = `<div class="dock-summary">${chips}</div>
    <div class="verification-grid">${rows || `<div class="empty-note">No verification checks are available.</div>`}</div>`;
}

function formatVersionTime(stamp) {
  const value = Number(stamp);
  if (!Number.isFinite(value) || !value) return "—";
  try { return new Date(value * 1000).toLocaleString(); } catch { return String(value); }
}

function renderTimelineDock() {
  const holder = $("#timelineDock");
  if (!holder) return;
  const versions = (STATE?.session?.versions || WORKSPACE.versions || []).slice(-8).reverse();
  holder.innerHTML = versions.length
    ? versions.map((version) => `<div class="dock-row">
        <span><b>${esc(version.label || `Version ${version.index ?? ""}`)}</b>
          <small>${esc(version.kind || "change")} · ${esc(formatVersionTime(version.at))} · ${esc(String(version.checksum || "").slice(0, 10))}</small></span>
        <span class="dock-actions"><button class="btn small" data-tool="history">Review</button></span>
      </div>`).join("")
    : `<div class="empty-note">No saved versions yet. Create a snapshot from History.</div>`;
}

function comparisonRows() {
  const result = DESIGN.optimisation;
  if (!result) return [];
  const ids = [...WORKSPACE.compare];
  if (!ids.length && result.winner) ids.push(result.winner);
  if (!ids.length) ids.push("baseline");
  return ids.map((id) => id === "baseline"
    ? result.baseline : (result.candidates || []).find((item) => item.id === id)).filter(Boolean);
}

function renderComparisonDock() {
  const holder = $("#comparisonDock");
  if (!holder) return;
  const result = DESIGN.optimisation;
  if (!result) {
    holder.innerHTML = `<div class="empty-note">Run the optimiser to compare real solved candidates against the baseline.</div>`;
    return;
  }
  const axes = result.axes || [];
  const rows = comparisonRows();
  const body = rows.map((row) => `<tr>
      <td><b>${esc(row.label || "Baseline")}</b></td>
      ${axes.map((name) => `<td>${metric(row.objective_values?.[name])}</td>`).join("")}
      <td>${metric(row.metrics?.mass_g)} g</td>
      <td>${metric(row.verification?.fail ?? row.failing_checks, 0)}</td>
      <td>${row.feasible === false ? "constraint failure" : "constraints pass"}</td>
    </tr>`).join("");
  holder.innerHTML = `<table class="compare-table">
      <thead><tr><th>Design</th>${axes.map((name) =>
        `<th>${esc(optMeta(name, result).label || name)}</th>`).join("")}<th>Mass</th><th>Fails</th><th>Status</th></tr></thead>
      <tbody>${body}</tbody>
    </table>
    <div class="dock-row"><span>Preview and apply actions remain explicit.</span>
      <span class="dock-actions"><button class="btn small" data-tool="compare-candidates">Compare candidates</button></span></div>`;
}

function setDock(name, collapsed = WORKSPACE.dockCollapsed) {
  if (["verification", "timeline", "comparison"].includes(name)) WORKSPACE.dock = name;
  WORKSPACE.dockCollapsed = Boolean(collapsed);
  const dock = $("#bottomDock");
  if (dock) dock.classList.toggle("collapsed", WORKSPACE.dockCollapsed);
  $$("#dockTabs button").forEach((button) =>
    button.classList.toggle("active", button.dataset.dock === WORKSPACE.dock));
  for (const [key, id] of [["verification", "verificationDock"],
    ["timeline", "timelineDock"], ["comparison", "comparisonDock"]]) {
    const panel = $(`#${id}`);
    if (panel) panel.classList.toggle("active", key === WORKSPACE.dock);
  }
  try { window.localStorage?.setItem("watchcad.dock", WORKSPACE.dock); } catch { /* ignore */ }
  if (WORKSPACE.dockCollapsed) ensureViewerSize();
  requestRender();
}

function restoreDock() {
  let saved = "";
  try { saved = window.localStorage?.getItem("watchcad.dock") || ""; } catch { /* ignore */ }
  setDock(saved || "verification", false);
}

/* --------------------------------------------------------------- critic */
function componentLabel(value) {
  if (!value) return "Whole design";
  if (typeof value === "string") return value;
  return value.label || value.name || value.component || value.id || "Selected component";
}

function componentSelection(value) {
  if (!value) return null;
  if (typeof value === "string") return { name: value };
  const selection = {};
  for (const key of ["name", "role", "group", "component", "id"]) {
    if (value[key]) selection[key] = value[key];
  }
  return Object.keys(selection).length ? selection : null;
}

function componentQuery(detail) {
  if (!detail) return "";
  if (typeof detail === "string") return detail;
  return detail.name || detail.component || detail.id || "";
}

function componentById(id) {
  return TREE.components.find((component) => component.id === id) || null;
}

function componentForDetail(detail) {
  if (!detail) return null;
  const id = detail.id || detail.component;
  if (id) {
    const exact = componentById(id);
    if (exact) return exact;
  }
  const name = String(detail.name || detail.part || "").toLowerCase();
  if (!name) return null;
  return TREE.components.find((component) =>
    (component.parts || []).some((part) => String(part).toLowerCase() === name)
    || (component.aliases || []).some((alias) => String(alias).toLowerCase() === name)) || null;
}

function partsForComponent(component) {
  const names = new Set(Object.values(V.tree || {}).flat());
  const parts = Array.isArray(component?.parts) ? component.parts.slice() : [];
  return names.size ? parts.filter((name) => names.has(name)) : parts;
}

function setPartHighlight(names) {
  V.highlightParts = new Set(names || []);
  emitVisibilityState();
  requestRender();
}

async function setComponentFocus(detail) {
  CRITIC.focus = componentSelection(detail);
  const component = componentForDetail(detail);
  V.selectedComponent = component ? component.id : null;
  setPartHighlight(component ? partsForComponent(component) : []);
  renderFocusChip();
  renderComponentTree();
  if (!CRITIC.focus) {
    CRITIC.context = null;
    CRITIC.report = null;
    CRITIC.error = "";
    renderCritic();
    return;
  }
  await refreshCritic();
}

async function refreshCritic() {
  const query = componentQuery(CRITIC.focus);
  const suffix = query ? `?component=${encodeURIComponent(query)}` : "";
  CRITIC.loading = true;
  CRITIC.error = "";
  renderCritic();
  try {
    const [context, report] = await Promise.all([
      query ? api(`/api/component/context${suffix}`) : Promise.resolve(null),
      api(`/api/critic${suffix}`),
    ]);
    CRITIC.context = context;
    CRITIC.report = report;
  } catch (error) {
    CRITIC.error = error.message;
  } finally {
    CRITIC.loading = false;
    renderCritic();
  }
}

function renderFocusChip() {
  const chip = $("#focusChip");
  const assistant = $("#assistantFocus");
  const focus = CRITIC.focus;
  const label = componentLabel(focus);
  if (chip) {
    chip.hidden = !focus;
    const name = $("#focusName");
    if (name) name.textContent = label;
  }
  if (assistant) {
    assistant.hidden = !focus;
    const name = $("#assistantFocusName");
    if (name) name.textContent = label;
  }
}

function renderCritic() {
  const summary = $("#criticSummary");
  const focusBox = $("#criticFocus");
  const axesBox = $("#criticAxes");
  const notesBox = $("#criticNotes");
  if (!summary || !focusBox || !axesBox || !notesBox) return;

  if (CRITIC.loading && !CRITIC.report) {
    summary.innerHTML = `<div class="empty-note">Running the deterministic critic…</div>`;
    focusBox.innerHTML = "";
    axesBox.innerHTML = "";
    notesBox.innerHTML = "";
    return;
  }
  if (CRITIC.error) {
    summary.innerHTML = `<div class="critic-error">Critic unavailable: ${esc(CRITIC.error)}</div>`;
    focusBox.innerHTML = "";
    axesBox.innerHTML = "";
    notesBox.innerHTML = "";
    return;
  }
  const report = CRITIC.report;
  if (!report) {
    summary.innerHTML = `<div class="empty-note">Select a part in the 3-D viewer or open the
      Critic tab to run the deterministic five-axis report.</div>`;
    focusBox.innerHTML = "";
    axesBox.innerHTML = "";
    notesBox.innerHTML = "";
    return;
  }

  const overall = report.overall || {};
  summary.innerHTML = `<div class="critic-score">
      <div class="critic-number ${esc(overall.band || "")}">${esc(overall.score ?? "—")}</div>
      <div class="critic-score-copy">
        <b>${esc(overall.band || "unrated")}</b>
        <span>${esc(overall.summary || "")}</span>
        <small>${esc(overall.note || "")}</small>
      </div>
    </div>`;

  const context = CRITIC.context;
  if (context) {
    const fields = (context.fields || []).map((field) => {
      const unit = field.unit ? ` ${esc(field.unit)}` : "";
      const lock = field.locked ? `<span class="critic-lock">locked</span>` : "";
      return `<div class="component-field">
        <span class="cf-path">${esc(field.path)}</span>
        <b>${esc(field.value ?? "—")}${unit}</b>${lock}
      </div>`;
    }).join("") || `<div class="empty-note">This component has no independent writable field.</div>`;
    const checks = (context.checks || []).map((check) => {
      const verdict = check.passed === false ? "fail" : (check.verdict || "calculated");
      return `<div class="component-check ${esc(verdict)}">
        <span>${esc(check.title || check.id)}</span>
        <b>${esc(verdict)}</b>
      </div>`;
    }).join("");
    const parts = (context.parts || []).map((part) =>
      `<span class="chip">${esc(part.name)}${part.count > 1 ? ` ×${part.count}` : ""}</span>`).join("");
    const notes = (context.notes || []).map((note) =>
      `<div class="component-note">${esc(note)}</div>`).join("");
    focusBox.innerHTML = `<div class="component-head">
        <div>
          <span class="eyebrow">Selected component</span>
          <h3>${esc(context.component?.label || componentLabel(CRITIC.focus))}</h3>
        </div>
        <span class="prov">${context.writable ? "editable focus" : "read-only"}</span>
      </div>
      <p class="component-desc">${esc(context.component?.description || "")}</p>
      <div class="component-fields">${fields}</div>
      ${checks ? `<div class="component-checks">${checks}</div>` : ""}
      ${parts ? `<div class="component-parts">${parts}</div>` : ""}
      ${notes}`;
  } else {
    focusBox.innerHTML = `<div class="component-head">
        <div><span class="eyebrow">Selected component</span><h3>Whole design</h3></div>
      </div>
      <p class="component-desc">No component is selected, so conversational edits are
      allowed across the whole writable specification. Select a part in the 3-D viewer
      to scope the next edit to that component.</p>`;
  }

  axesBox.innerHTML = (report.axes || []).map((axis, axisIndex) => {
    const score = Number(axis.score || 0);
    const metrics = (axis.metrics || []).map((metric) => {
      const unit = metric.unit ? ` ${esc(metric.unit)}` : "";
      return `<div class="metric ${esc(metric.provenance || "calculated")}">
        <span>${esc(metric.label)}</span>
        <b>${esc(metric.value ?? "—")}${unit}</b>
        <em>${esc(metric.provenance || "calculated")}</em>
      </div>`;
    }).join("");
    const findings = (axis.findings || []).map((finding) => {
      const focus = finding.in_focus ? " in-focus" : "";
      const owner = finding.component ? `<span class="finding-owner">${esc(finding.component)}</span>` : "";
      return `<li class="finding ${esc(finding.kind || "note")}${focus}">
        <span>${esc(finding.text || "")}</span>
        <span class="finding-meta">${owner}<em>${esc(finding.confidence || "calculated")}</em></span>
      </li>`;
    }).join("");
    const fixes = (axis.fixes || []).map((fix, fixIndex) => {
      const current = fix.current === null || fix.current === undefined
        ? "" : ` <small>(now ${esc(fix.current)})</small>`;
      return `<button class="btn small critic-fix${fix.in_focus ? " in-focus" : ""}"
        data-critic-fix="${axisIndex}:${fixIndex}"
        title="${esc(fix.reason || "")}">
        ${esc(fix.label || `Set ${fix.path}`)}${current}
      </button>`;
    }).join("");
    return `<section class="critic-axis">
      <div class="axis-head">
        <div>
          <h4>${esc(axis.label || axis.id)}</h4>
          <span class="prov">${esc(axis.confidence || "calculated")}</span>
        </div>
        <div class="axis-score ${esc(axis.band || "")}"><b>${esc(axis.score)}</b><span>${esc(axis.band || "")}</span></div>
      </div>
      <div class="score-bar"><i style="width:${Math.max(0, Math.min(100, score))}%"></i></div>
      <p class="axis-summary">${esc(axis.summary || "")}</p>
      ${findings ? `<ul class="findings">${findings}</ul>` : ""}
      ${metrics ? `<details class="axis-metrics"><summary>Metrics (${(axis.metrics || []).length})</summary>${metrics}</details>` : ""}
      ${fixes ? `<div class="axis-fixes">${fixes}</div>` : ""}
    </section>`;
  }).join("");

  const notes = (report.notes || []).map((note) => `<li>${esc(note)}</li>`).join("");
  notesBox.innerHTML = `<h4>How to read this report</h4><ul>${notes}</ul>`;
}

async function applyCriticFix(axisIndex, fixIndex) {
  const axis = CRITIC.report?.axes?.[Number(axisIndex)];
  const fix = axis?.fixes?.[Number(fixIndex)];
  if (!fix) return;
  if (fix.confidence === "estimated") {
    toast(`applying estimated fix: ${fix.path} = ${fix.value}`);
  } else {
    toast(`applying calculated fix: ${fix.path} = ${fix.value}`);
  }
  await setField(fix.path, fix.value);
  await refreshCritic();
}

/* -------------------------------------------------------------- package */
function renderPackage() {
  const holder = $("#packageBody");
  const build = STATE && STATE.build;
  if (!build) {
    holder.innerHTML = `<div class="build-empty">
      <p>Build the production package from the current spec: <b>STEP-ready meshes</b> (STL,
      OBJ, glTF), drawings (SVG, DXF, PDF), BOM, assembly sheet, test plan, DFM report,
      verification report and a checksummed manifest.</p>
      <p>STEP is reported honestly: the zero-dependency kernel writes triangle meshes and
      <code>step_status.json</code> says why a B-rep file is not present.</p>
      <button class="btn primary" data-build="1">Build package</button>
    </div>`;
    return;
  }
  const summary = build.summary || {};
  const bad = (summary.fail || 0) > 0;
  const stats = `<div class="build-stats">
    <div><b>${(build.files || []).length}</b><span>files</span></div>
    <div><b>${(build.geometry?.parts || 0)}</b><span>parts</span></div>
    <div><b>${((build.geometry?.triangles || 0) / 1000).toFixed(0)}k</b><span>triangles</span></div>
  </div>`;
  const banner = `<div class="build-banner ${bad ? "bad" : ""}">
    ${bad ? `${summary.fail} verification failure(s) are recorded in the package.` : "Package generated with no failing checks."}
    ${build.reused ? " Existing build reused (same checksum)." : ""}
  </div>`;
  const step = build.manifest?.step || {};
  const stepNote = step.status === "unavailable"
    ? `<div class="step-note">STEP unavailable: ${esc(step.reason || "no B-rep backend")}. ${esc(step.consequence || "")}</div>`
    : "";

  const byDir = new Map();
  const sizes = new Map((build.manifest?.files || []).map((file) => [file.path, file.bytes]));
  for (const path of build.files || []) {
    const parts = path.split("/");
    const dir = parts.length > 1 ? parts[0] : "package";
    if (!byDir.has(dir)) byDir.set(dir, []);
    byDir.get(dir).push(path);
  }
  const groups = [...byDir.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([dir, files]) => {
    const rows = files.sort().map((path) => {
      const name = path.split("/").slice(1).join("/") || path;
      const bytes = sizes.get(path);
      const size = bytes > 1024 * 1024 ? `${(bytes / 1048576).toFixed(1)} MB`
        : bytes > 1024 ? `${(bytes / 1024).toFixed(0)} kB` : `${bytes || 0} B`;
      return `<div class="file-row">
        <a href="${esc(withSession(`/api/download/file?path=${encodeURIComponent(path)}`))}" download>${esc(name)}</a>
        <span>${size}</span></div>`;
    }).join("");
    return `<div class="file-group"><h3>${esc(dir)}</h3>${rows}</div>`;
  }).join("");

  holder.innerHTML = `${banner}${stats}
    <div style="display:flex;gap:6px;flex-wrap:wrap">
      <a class="btn primary" href="${esc(withSession("/api/download/zip"))}" download>Download package.zip</a>
      <button class="btn" data-build="1">Build again</button>
    </div>
    <div style="margin-top:9px;font:10px var(--mono);color:var(--dim);word-break:break-all">
      ${esc(build.root || "")}
    </div>
    ${stepNote}${groups}
    <div class="empty-note">Every file carries a sha256 in manifest.json.</div>`;
}

async function buildPackage() {
  setLoading(true, "building the package");
  try {
    const state = await api("/api/build", { per_part: true });
    applyState(state, { scene: false });
    switchTab("package");
    toast(`package built: ${(state.build?.files || []).length} files`);
  } catch (error) {
    toast(error.message, true);
  } finally {
    setLoading(false);
  }
}

/* ---------------------------------------------------------------- files */
function saveSpec() {
  if (!STATE) return;
  const blob = new Blob([JSON.stringify(STATE.spec, null, 2)], { type: "application/json" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = `${slug(STATE.spec.data.meta.name)}.json`;
  link.click();
  URL.revokeObjectURL(link.href);
}

async function loadSpecFile(event) {
  const file = event.target.files && event.target.files[0];
  if (!file) return;
  try {
    const text = await file.text();
    const parsed = JSON.parse(text);
    setLoading(true, "loading the spec");
    const state = await api("/api/spec", { spec: parsed });
    CHAT.prevSpec = null;
    CHAT.messages.push({ role: "assistant", lead: `Loaded ${file.name}. The spec and the mesh are in sync.` });
    applyState(state, { scene: true });
    renderChat();
    toast(`loaded ${file.name}`);
  } catch (error) {
    toast(error.message, true);
  } finally {
    setLoading(false);
    event.target.value = "";
  }
}

async function resetSpec() {
  if (!confirm("Start a new spec? Unsaved changes in this session will be lost.")) return;
  setLoading(true, "resetting");
  try {
    const state = await api("/api/reset", { name: "Untitled" });
    CHAT.prevSpec = null;
    CHAT.messages.push({ role: "assistant", lead: "Fresh spec. What kind of watch are we making?" });
    applyState(state, { scene: true });
    renderChat();
    toast("new spec");
  } catch (error) {
    toast(error.message, true);
  } finally {
    setLoading(false);
  }
}

/* -------------------------------------------------------------- actions */
function panelPreference(side) {
  try {
    return window.localStorage?.getItem(`watchcad.panel.${side}`) || null;
  } catch {
    return null;
  }
}

function setPanelClosed(side, closed) {
  const panel = $(side === "left" ? "#leftPanel" : "#rightPanel");
  const rail = $(side === "left" ? "#railSpecs" : "#railCopilot");
  const layout = $("#layout");
  if (!panel || !layout) return;
  const clipped = Boolean(closed);
  panel.classList.toggle("panel-collapsed", clipped);
  panel.setAttribute?.("aria-hidden", String(clipped));
  if ("inert" in panel) panel.inert = clipped;
  if (rail) {
    // The rail stays visible in both states: it opens a closed sidebar and
    // closes an open one. Its position tracks the panel edge via CSS.
    rail.hidden = false;
    rail.dataset.state = clipped ? "closed" : "open";
    rail.setAttribute?.("aria-expanded", String(!clipped));
    rail.title = clipped
      ? `Show ${side === "left" ? "the specs" : "the AI agent"} sidebar`
      : `Hide ${side === "left" ? "the specs" : "the AI agent"} sidebar`;
  }
  layout.classList.toggle(`hide-${side}`, clipped);
  try {
    window.localStorage?.setItem(`watchcad.panel.${side}`, clipped ? "closed" : "open");
  } catch { /* private mode or a non-browser harness */ }
  ensureViewerSize();
  requestRender();
}

function restorePanels() {
  setPanelClosed("left", panelPreference("left") === "closed");
  setPanelClosed("right", panelPreference("right") === "closed");
}

function onGlobalKeydown(event) {
  if (!event || event.defaultPrevented) return;
  const editable = typeof event.target?.closest === "function"
    ? event.target.closest("input, textarea, select, [contenteditable='true']") : null;
  const key = String(event.key || "");
  const mod = Boolean(event.ctrlKey || event.metaKey);

  if (key === "Escape") {
    for (const id of ["movementModal", "historyModal", "compareModal"]) {
      if ($(`#${id}`)?.hidden === false) {
        hideModal(id);
        event.preventDefault();
        return;
      }
    }
    if (CHAT.settingsOpen) { toggleSettings(); event.preventDefault(); return; }
    if ($("#chatSidebar")?.hidden === false) { toggleChatHistory(); event.preventDefault(); return; }
    if (CRITIC.focus || V.selected !== null) {
      clearPartSelection();
      event.preventDefault();
    }
    return;
  }
  if (editable) return;

  if (mod && key.toLowerCase() === "z") {
    event.preventDefault();
    if (event.shiftKey) redoTurn(); else undoTurn();
    return;
  }
  if (mod && key.toLowerCase() === "y") {
    event.preventDefault();
    redoTurn();
    return;
  }
  if (mod || event.altKey) return;

  if (key === "/") {
    event.preventDefault();
    const search = $("#componentSearch");
    setPanelClosed("left", false);
    search?.focus?.();
    return;
  }
  if (key === "1" || key === "2" || key === "3") {
    event.preventDefault();
    setWorkspace(["design", "engineering", "optimise"][Number(key) - 1]);
    return;
  }
  if (key.toLowerCase() === "h") {
    event.preventDefault();
    openHistoryModal();
    return;
  }
  if (key === "+" || key === "=") {
    event.preventDefault();
    zoomBy(0.8);
    return;
  }
  if (key === "-" || key === "_") {
    event.preventDefault();
    zoomBy(1.25);
  }
}

function onGlobalClick(event) {
  if (!event?.target?.closest) return;
  // Scope this to buttons: <body> carries data-workspace, so a bare
  // [data-workspace] match would hijack every click in the document.
  const workspace = event.target.closest("button[data-workspace]");
  if (workspace) { setWorkspace(workspace.dataset.workspace); return; }

  const tool = event.target.closest("[data-tool]");
  if (tool) {
    const action = tool.dataset.tool;
    if (action === "history") openHistoryModal();
    else if (action === "movement-dialog") openMovementModal();
    else if (action === "compare-candidates") openCompareModal();
    else if (action === "optimise") runOptimisation();
    else if (action === "clear-preview") clearCandidatePreview();
    else if (action === "variations") generateVariations();
    else if (action === "add-reference") $("#designReference")?.click?.();
    return;
  }
  if (event.target.closest("[data-close-movement]")) { hideModal("movementModal"); return; }
  if (event.target.closest("[data-close-history]")) { hideModal("historyModal"); return; }
  if (event.target.closest("[data-close-compare]")) { hideModal("compareModal"); return; }
  const dockTab = event.target.closest("[data-dock]");
  if (dockTab) { setDock(dockTab.dataset.dock); return; }
  if (event.target.closest("[data-dock-toggle]")) {
    setDock(WORKSPACE.dock, !WORKSPACE.dockCollapsed);
    return;
  }

  const treeExpand = event.target.closest("[data-tree-expand]");
  if (treeExpand) {
    const id = treeExpand.dataset.treeExpand;
    if (TREE.expanded.has(id)) TREE.expanded.delete(id); else TREE.expanded.add(id);
    renderComponentTree();
    return;
  }
  const treeSelect = event.target.closest("[data-tree-select]");
  if (treeSelect) { selectComponent(treeSelect.dataset.treeSelect); return; }
  const treePart = event.target.closest("[data-tree-part]");
  if (treePart) { selectPartByName(treePart.dataset.treePart); return; }
  const treeVisibility = event.target.closest("[data-tree-visibility]");
  if (treeVisibility) {
    const component = componentById(treeVisibility.dataset.treeVisibility);
    const parts = partsForComponent(component);
    const hidden = parts.length && parts.every((part) => V.hiddenParts.has(part));
    setComponentVisibility(component, hidden);
    return;
  }
  const treePartVisibility = event.target.closest("[data-tree-part-visibility]");
  if (treePartVisibility) {
    const part = treePartVisibility.dataset.treePartVisibility;
    if (V.hiddenParts.has(part)) V.hiddenParts.delete(part); else V.hiddenParts.add(part);
    emitVisibilityState();
    renderComponentTree();
    requestRender();
    return;
  }
  const treeIsolate = event.target.closest("[data-tree-isolate]");
  if (treeIsolate) { isolateComponent(componentById(treeIsolate.dataset.treeIsolate)); return; }
  const treeLock = event.target.closest("[data-tree-lock]");
  if (treeLock) { toggleComponentLock(treeLock.dataset.treeLock); return; }

  const versionCompare = event.target.closest("[data-version-compare]");
  if (versionCompare) { selectVersion(versionCompare.dataset.versionCompare); return; }
  const versionRestore = event.target.closest("[data-version-restore]");
  if (versionRestore) { armVersionRestore(versionRestore.dataset.versionRestore); return; }
  const confirmRestore = event.target.closest("[data-confirm-restore]");
  if (confirmRestore) { restoreVersion(confirmRestore.dataset.confirmRestore); return; }
  if (event.target.closest("[data-cancel-restore]")) {
    WORKSPACE.restoreId = null;
    const confirm = $("#restoreConfirm");
    if (confirm) confirm.hidden = true;
    return;
  }
  const compareToggle = event.target.closest("[data-compare-toggle]");
  if (compareToggle) {
    const id = compareToggle.dataset.compareToggle;
    if (WORKSPACE.compare.has(id)) WORKSPACE.compare.delete(id); else WORKSPACE.compare.add(id);
    renderCandidateCompare();
    renderComparisonDock();
    return;
  }

  const dialSwatch = event.target.closest("[data-dial-color]");
  if (dialSwatch) { setField("dial.color", dialSwatch.dataset.dialColor); return; }
  const confirmCandidate = event.target.closest("[data-confirm-candidate]");
  if (confirmCandidate) { applyCandidate(confirmCandidate.dataset.confirmCandidate); return; }
  if (event.target.closest("[data-cancel-apply]")) {
    DESIGN.pendingApply = null;
    renderOptimisation();
    return;
  }
  if (event.target.closest("[data-clear-preview]")) { clearCandidatePreview(); return; }
  if (event.target.closest("[data-undo-candidate]")) { undoTurn(); return; }
  const candidate = event.target.closest("[data-apply-candidate]");
  if (candidate) { armCandidateApply(candidate.dataset.applyCandidate); return; }
  const previewCandidateButton = event.target.closest("[data-preview-candidate]");
  if (previewCandidateButton) {
    previewCandidate(previewCandidateButton.dataset.previewCandidate);
    return;
  }
  const inspected = event.target.closest("[data-select-candidate]")
    || event.target.closest("[data-inspect-candidate]");
  if (inspected) {
    DESIGN.selectedCandidate = inspected.dataset.selectCandidate
      || inspected.dataset.inspectCandidate;
    renderOptimisation();
    return;
  }
  const movement = event.target.closest("[data-movement]");
  if (movement) { loadMovementDetail(movement.dataset.movement); return; }
  const movementPropose = event.target.closest("[data-movement-propose]");
  if (movementPropose) { proposeMovement(); return; }
  const movementApply = event.target.closest("[data-movement-apply]");
  if (movementApply) { applyMovementProposal(movementApply.dataset.movementApply); return; }
  if (event.target.closest("[data-movement-cancel]")) {
    MOVEMENT.proposal = null;
    renderMovement();
    return;
  }
  const provider = event.target.closest("[data-provider]");
  if (provider) { selectProvider(provider.dataset.provider); return; }
  if (event.target.closest("[data-close-provider]")) { closeProviders(); return; }
  const clearPart = event.target.closest("[data-part-clear]");
  if (clearPart) { clearPartSelection(); return; }
  const focusClear = event.target.closest("[data-focus-clear]");
  if (focusClear) { clearPartSelection(); return; }
  const criticFix = event.target.closest("[data-critic-fix]");
  if (criticFix) {
    const [axisIndex, fixIndex] = String(criticFix.dataset.criticFix || "").split(":");
    applyCriticFix(axisIndex, fixIndex);
    return;
  }
  const panelToggle = event.target.closest("[data-panel-toggle]");
  if (panelToggle) {
    const side = panelToggle.dataset.panelToggle;
    const panel = $(side === "left" ? "#leftPanel" : "#rightPanel");
    if (panel) setPanelClosed(side, !panel.classList.contains("panel-collapsed"));
    return;
  }
  const closer = event.target.closest("[data-close]");
  if (closer) { setPanelClosed(closer.dataset.close, true); return; }
  const opener = event.target.closest("[data-open]");
  if (opener) { setPanelClosed(opener.dataset.open, false); return; }
  const tab = event.target.closest("[data-tab]");
  if (tab) { switchTab(tab.dataset.tab); return; }
  const zoom = event.target.closest("[data-zoom]");
  if (zoom) { zoomBy(zoom.dataset.zoom === "in" ? 0.8 : 1.25); return; }
  const view = event.target.closest("[data-view]");
  if (view) { setView(view.dataset.view); return; }
  const openConversationButton = event.target.closest("[data-open-conversation]");
  if (openConversationButton) {
    openConversation(openConversationButton.dataset.openConversation);
    return;
  }
  const renameConversationButton = event.target.closest("[data-rename-conversation]");
  if (renameConversationButton) {
    renameConversation(renameConversationButton.dataset.renameConversation);
    return;
  }
  const deleteConversationButton = event.target.closest("[data-delete-conversation]");
  if (deleteConversationButton) {
    deleteConversation(deleteConversationButton.dataset.deleteConversation);
    return;
  }
  const removeAttachment = event.target.closest("[data-remove-attachment]");
  if (removeAttachment) {
    CHAT.attachments.splice(Number(removeAttachment.dataset.removeAttachment), 1);
    renderAttachments();
    return;
  }
  const copyMessageButton = event.target.closest("[data-copy-msg]");
  if (copyMessageButton) { copyMessage(Number(copyMessageButton.dataset.copyMsg)); return; }
  const editMessageButton = event.target.closest("[data-edit-msg]");
  if (editMessageButton) { startEditMessage(Number(editMessageButton.dataset.editMsg)); return; }
  const regenerateMessageButton = event.target.closest("[data-regenerate-msg]");
  if (regenerateMessageButton) { regenerateChat(); return; }
  const deleteMessageButton = event.target.closest("[data-delete-msg]");
  if (deleteMessageButton) { deleteChatMessage(Number(deleteMessageButton.dataset.deleteMsg)); return; }
  const suggest = event.target.closest("[data-suggest]");
  if (suggest) { sendChat(suggest.dataset.suggest); return; }
  const undo = event.target.closest("[data-undo]");
  if (undo) { undoPrompt(); return; }
  const variant = event.target.closest("[data-variant]");
  if (variant) { applyVariant(variant.dataset.variant); return; }
  const approve = event.target.closest("[data-approve]");
  if (approve) { approveAgent(true); return; }
  const reject = event.target.closest("[data-reject]");
  if (reject) { approveAgent(false); return; }
  const historyClose = event.target.closest("[data-history-close]");
  if (historyClose) { const box = $("#agentHistory"); if (box) box.hidden = true; return; }
  const lock = event.target.closest("[data-lock]");
  if (lock) { toggleLock(lock.dataset.lock); return; }
  const filter = event.target.closest("[data-filter]");
  if (filter) { checkFilter = filter.dataset.filter; renderChecks(); return; }
  const fix = event.target.closest("[data-fix]");
  if (fix) { applyFix(fix.dataset.fix); return; }
  const message = event.target.closest("[data-message]");
  if (message) {
    const check = (STATE.checks || [])[Number(message.dataset.message)];
    if (check?.fix?.message) toast(check.fix.message);
    return;
  }
  const build = event.target.closest("[data-build]");
  if (build) { buildPackage(); return; }
  const group = event.target.closest("[data-group]");
  if (group) {
    const name = group.dataset.group;
    if (V.isolate && !V.isolate.has(name)) {
      V.isolate = null;
      V.hidden.delete(name);
    } else if (V.hidden.has(name)) {
      V.hidden.delete(name);
    } else {
      V.hidden.add(name);
    }
    renderGroupToggles(V.tree);
    keepSelectionVisible();
    updateLayerInfo();
    emitVisibilityState();
    requestRender();
  }
}

function switchTab(name) {
  activeTab = name;
  $$(".tabs button").forEach((button) =>
    button.classList.toggle("active", button.dataset.tab === name));
  $$(".tab-panel").forEach((panel) =>
    panel.classList.toggle("active", panel.id === `tab-${name}`));
  if (name === "critic" && !CRITIC.report && !CRITIC.loading) refreshCritic();
  if (name === "movement" && !MOVEMENT.catalog) loadMovementCatalog();
}

/* --------------------------------------------------------------- viewer */
/* The Three.js module is a separate ES module.  app.js talks to it only
   through events, so the 2D renderer remains a complete fallback when WebGL
   or module support is missing. */
function viewerEvent(name, detail) {
  try {
    if (typeof CustomEvent === "function" && window?.dispatchEvent) {
      window.dispatchEvent(new CustomEvent(name, { detail }));
    }
  } catch { /* non-browser harness */ }
}

function emitVisibilityState() {
  viewerEvent("watchcad:visibility", {
    hidden: [...V.hidden],
    isolate: V.isolate ? [...V.isolate] : null,
    parts_hidden: [...V.hiddenParts],
    parts_isolate: V.isolateParts ? [...V.isolateParts] : null,
    highlight: [...V.highlightParts],
  });
}

function bindViewerBridge() {
  if (typeof window?.addEventListener !== "function") return;
  window.addEventListener("watchcad:select-part", (event) => {
    const detail = event.detail;
    if (!detail || !detail.name) {
      setComponentFocus(null);
      if (V.selected !== null) clearPartSelection();
      return;
    }
    setComponentFocus(detail);
    const index = (V.mesh?.ranges || []).findIndex((range) => range.name === detail.name);
    if (index >= 0) {
      V.selected = index;
      updatePartHud();
      updateViewerCursor();
      requestRender(false);
      return;
    }
    const hud = $("#partHud");
    const name = $("#partName");
    const meta = $("#partMeta");
    if (name) name.textContent = detail.name;
    if (meta) meta.textContent = `${detail.role || "part"} · ${detail.group || "Parts"}`;
    if (hud) hud.hidden = false;
  });
  window.addEventListener("watchcad:viewer-error", (event) => {
    const message = event.detail?.message;
    if (message) toast(`3D view unavailable: ${message}`, true);
    requestRender();
  });
  window.addEventListener("watchcad:viewer-model", (event) => {
    renderedRevision = Number(event.detail?.revision || 0) || renderedRevision;
    // A rebuilt GLB resets the module's local explode/visibility state, so
    // replay the app's authoritative viewer state.
    emitVisibilityState();
    viewerEvent("watchcad:explode", { value: V.explode });
    renderHeader();
  });
  window.addEventListener("watchcad:viewer-stale", (event) => {
    const detail = event.detail || {};
    setStatusText(`stale model ignored (revision ${detail.received || "?"})`);
  });
}

function setStatusText(text) {
  const node = $("#viewerInfo");
  if (node) node.textContent = text;
}

const V = {
  yaw: -1.10,
  pitch: 1.02,
  dist: 90,
  panX: 0,
  panY: 0,
  radius: 25,
  focus: [0, 0, 0],
  focusRadius: 25,
  explode: 0,
  layerGap: 2,
  isolate: null,
  isolateParts: null,
  tree: {},
  ready: false,
  hidden: new Set(),
  hiddenParts: new Set(),
  highlightParts: new Set(),
  selectedComponent: null,
  selected: null,
  hover: null,
  hoverTimer: 0,
  dragging: null,
  lastX: 0,
  lastY: 0,
  downX: 0,
  downY: 0,
  moved: false,
  fast: false,
  canvas: null,
  ctx: null,
  mesh: null,
  projection: null,
  pending: false,
};

function setupViewer() {
  const canvas = $("#viewer");
  const ctx = canvas.getContext("2d");
  V.canvas = canvas;
  V.ctx = ctx;

  canvas.addEventListener("pointerdown", (event) => {
    if (event.button !== 0 && event.button !== 1 && event.button !== 2) return;
    canvas.setPointerCapture(event.pointerId);
    V.dragging = event.button === 0 && !event.shiftKey ? "orbit" : "pan";
    V.lastX = event.clientX;
    V.lastY = event.clientY;
    V.downX = event.clientX;
    V.downY = event.clientY;
    V.moved = false;
    V.hover = null;
    updateViewerCursor();
    canvas.classList.add("dragging");
  });
  canvas.addEventListener("pointermove", (event) => {
    if (!V.dragging) {
      scheduleHover(event.clientX, event.clientY);
      return;
    }
    const dx = event.clientX - V.lastX;
    const dy = event.clientY - V.lastY;
    if (Math.hypot(event.clientX - V.downX, event.clientY - V.downY) > 3) V.moved = true;
    V.lastX = event.clientX;
    V.lastY = event.clientY;
    if (V.dragging === "orbit") {
      V.yaw -= dx * 0.008;
      V.pitch = Math.max(-1.45, Math.min(1.553, V.pitch + dy * 0.008));
    } else {
      V.panX += dx;
      V.panY += dy;
    }
    requestRender(true);
  });
  const endDrag = (event, allowPick) => {
    if (!V.dragging) return;
    const wasOrbit = V.dragging === "orbit";
    const clicked = allowPick && wasOrbit && !V.moved && event.button === 0;
    V.dragging = null;
    canvas.classList.remove("dragging");
    if (canvas.hasPointerCapture?.(event.pointerId)) canvas.releasePointerCapture(event.pointerId);
    updateViewerCursor();
    if (clicked) selectPartAt(event.clientX, event.clientY);
    else requestRender(false);
  };
  canvas.addEventListener("pointerup", (event) => endDrag(event, true));
  canvas.addEventListener("pointercancel", (event) => endDrag(event, false));
  canvas.addEventListener("pointerleave", () => {
    if (V.dragging) return;
    clearTimeout(V.hoverTimer);
    if (V.hover !== null) { V.hover = null; updateViewerCursor(); requestRender(); }
  });
  canvas.addEventListener("wheel", (event) => {
    event.preventDefault();
    const rect = canvas.getBoundingClientRect();
    zoomAt(event.clientX - rect.left, event.clientY - rect.top,
      Math.exp(event.deltaY * 0.0012), rect);
  }, { passive: false });
  canvas.addEventListener("contextmenu", (event) => event.preventDefault());
  canvas.addEventListener("dblclick", () => setView("three"));
  window.addEventListener("resize", () => { ensureViewerSize(); requestRender(); });
  window.addEventListener("hashchange", setViewFromHash);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && V.selected !== null) clearPartSelection();
  });
  if (window.ResizeObserver) {
    new ResizeObserver(() => { ensureViewerSize(); requestRender(); }).observe(canvas.parentElement);
  }
}

function ensureViewerSize() {
  const canvas = V.canvas;
  if (!canvas) return;
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const w = Math.max(1, Math.floor(canvas.clientWidth * dpr));
  const h = Math.max(1, Math.floor(canvas.clientHeight * dpr));
  if (canvas.width !== w || canvas.height !== h) {
    canvas.width = w;
    canvas.height = h;
  }
}

function zoomAt(sx, sy, factor, rect) {
  rect = rect || V.canvas.getBoundingClientRect();
  const old = V.dist || 90;
  const radius = V.focusRadius || V.radius || 20;
  const min = Math.max(0.25, radius * 0.06);
  const max = Math.max(min * 20, radius * 40);
  const next = Math.max(min, Math.min(max, old * factor));
  const k = next / old;
  const offsetX = sx - (rect.width / 2 + V.panX);
  const offsetY = sy - (rect.height / 2 + V.panY);
  if (Number.isFinite(k) && k > 0 && k !== 1) {
    // Keep the model point under the cursor fixed while the distance changes.
    V.panX += offsetX * (1 - 1 / k);
    V.panY += offsetY * (1 - 1 / k);
  }
  V.dist = next;
  requestRender();
}

function zoomBy(factor) {
  viewerEvent("watchcad:zoom", { factor });
  if (!V.canvas) return;
  const rect = V.canvas.getBoundingClientRect();
  zoomAt(rect.width / 2, rect.height / 2, factor, rect);
}

function isVisible(group) {
  if (V.hidden.has(group)) return false;
  return !V.isolate || V.isolate.has(group);
}

function isRangeVisible(range) {
  if (!range || !isVisible(range.group)) return false;
  if (V.hiddenParts.has(range.name)) return false;
  if (V.isolateParts && !V.isolateParts.has(range.name)) return false;
  return true;
}

function visibleBounds() {
  const mesh = V.mesh;
  if (!mesh) return null;
  const min = [Infinity, Infinity, Infinity];
  const max = [-Infinity, -Infinity, -Infinity];
  let any = false;
  for (const range of mesh.ranges) {
    if (!isRangeVisible(range) || !range.box) continue;
    const box = range.box;
    const shift = (mesh.bandZ[range.band] - mesh.midBand) * V.explode * mesh.layerGap;
    min[0] = Math.min(min[0], box[0]); max[0] = Math.max(max[0], box[1]);
    min[1] = Math.min(min[1], box[2]); max[1] = Math.max(max[1], box[3]);
    min[2] = Math.min(min[2], box[4] + shift); max[2] = Math.max(max[2], box[5] + shift);
    any = true;
  }
  return any ? { min, max } : null;
}

function fitView() {
  const bounds = visibleBounds();
  if (!bounds) return;
  V.focus = [0, 1, 2].map((i) => (bounds.min[i] + bounds.max[i]) / 2);
  const size = [0, 1, 2].map((i) => Math.max(0.5, bounds.max[i] - bounds.min[i]));
  V.focusRadius = Math.max(2, Math.hypot(size[0], size[1], size[2]) / 2);
  V.dist = V.focusRadius * 3.2;
  V.panX = 0;
  V.panY = 0;
}

function setExplodeValue(percent, fit = true) {
  const value = Math.max(0, Math.min(250, Number(percent) || 0));
  V.explode = value / 100;
  const input = $("#explode");
  const rounded = Math.round(value);
  if (input && Number(input.value) !== rounded) input.value = String(rounded);
  const label = $("#explodeValue");
  if (label) label.textContent = `${rounded}%`;
  if (fit) fitView();
  updateLayerInfo();
  viewerEvent("watchcad:explode", { value: V.explode });
  requestRender();
}

function updateLayerInfo() {
  const mesh = V.mesh;
  const info = $("#layerInfo");
  if (!mesh || !info) return;
  const bands = new Set();
  let parts = 0;
  for (const range of mesh.ranges) {
    if (!isRangeVisible(range)) continue;
    bands.add(range.band);
    parts += 1;
  }
  info.textContent = `${bands.size}/${mesh.bandCount} layers · ${parts} parts`;
}

function requestRender(fast = false) {
  if (fast) V.fast = true;
  if (V.pending) return;
  V.pending = true;
  requestAnimationFrame(() => {
    V.pending = false;
    render3d();
  });
}

async function loadScene(initial = false) {
  const token = ++sceneToken;
  const requestedRevision = appliedRevision || Number(STATE?.revision || 0);
  try {
    if (initial) setLoading(true, "loading the mesh");
    const payload = await api(revisionedUrl("/api/scene", requestedRevision));
    if (token !== sceneToken) return;
    const received = Number(payload.revision || responseRevision(payload) || 0);
    if (requestedRevision && received && received !== requestedRevision) return;
    lastSceneSig = sceneSignature(STATE);
    prepareMesh(payload);
    renderGroupToggles(payload.tree || {});
    viewerEvent("watchcad:scene-changed", {
      session: String(payload.session_id || currentSession()),
      revision: received || requestedRevision,
      checksum: String(payload.checksum || sceneSignature(STATE) || ""),
      modification_id: String(payload.modification_id
        || responseHeader(payload, "X-Modification-ID")
        || STATE?.modification_id || ""),
      preview: Boolean(payload.preview),
      preview_id: String(payload.preview_id || ""),
    });
    requestRender();
  } catch (error) {
    toast(`mesh load failed: ${error.message}`, true);
  } finally {
    if (initial) setLoading(false);
  }
}

function sceneSignature(state) {
  if (!state) return "";
  return `${state.spec?.data?.derived?.checksum || ""}|${state.scene?.triangles || 0}`;
}

function scheduleScene() {
  const signature = sceneSignature(STATE);
  if (signature === lastSceneSig) return;
  clearTimeout(sceneTimer);
  sceneTimer = setTimeout(() => loadScene(false), 260);
}

function prepareMesh(payload) {
  const bounds = payload.bounds || {};
  const min = bounds.min || [0, 0, 0];
  const max = bounds.max || [0, 0, 0];
  const center = [0, 1, 2].map((i) => ((min[i] || 0) + (max[i] || 0)) / 2);
  const size = [0, 1, 2].map((i) => (max[i] || 0) - (min[i] || 0));
  V.radius = Math.max(...size) / 2 || 20;
  V.tree = payload.tree || {};
  const previousSelection = V.selected !== null && V.mesh?.ranges?.[V.selected]?.name;

  const groupOf = {};
  for (const [group, names] of Object.entries(V.tree)) {
    for (const name of names) groupOf[name] = group;
  }
  const roleGroup = {
    case: "Case", dial: "Dial", hand: "Hands", movement: "Movement",
    complication: "Complications", crystal: "Crystal", gasket: "Case",
  };

  const valid = (payload.parts || []).filter((part) =>
    (part.verts || []).length >= 9 && (part.faces || []).length >= 3);
  const partZ = valid.map((part) => {
    const box = part.bounds;
    if (box && box.min && box.max) return (box.min[2] + box.max[2]) / 2 - center[2];
    let low = Infinity, high = -Infinity;
    for (let i = 2; i < part.verts.length; i += 3) {
      low = Math.min(low, part.verts[i]);
      high = Math.max(high, part.verts[i]);
    }
    return ((low + high) / 2 - center[2]) || 0;
  });

  // Cluster part centres into horizontal layers. Each layer moves as one rigid band.
  const unique = [...new Set(partZ.map((z) => Math.round(z * 1000) / 1000))].sort((a, b) => a - b);
  const bands = [];
  for (const z of unique) {
    const last = bands[bands.length - 1];
    if (last && Math.abs(z - last.center) <= 0.08) {
      last.sum += z; last.n += 1; last.center = last.sum / last.n;
    } else {
      bands.push({ center: z, sum: z, n: 1 });
    }
  }
  const bandIndexFor = (z) => {
    let best = 0, distance = Infinity;
    bands.forEach((band, index) => {
      const delta = Math.abs(band.center - z);
      if (delta < distance) { distance = delta; best = index; }
    });
    return best;
  };
  const bandZ = new Float32Array(bands.map((band) => band.center));
  const midBand = bands.length ? (bands[0].center + bands[bands.length - 1].center) / 2 : 0;

  const positions = [];
  const indices = [];
  const vertBandList = [];
  const triBandList = [];
  const triRangeList = [];
  const ranges = [];
  let vertexBase = 0;
  let triangleBase = 0;

  for (let p = 0; p < valid.length; p += 1) {
    const part = valid[p];
    const verts = part.verts;
    const faces = part.faces;
    const group = groupOf[part.name] || roleGroup[part.role] || "Other";
    const band = bandIndexFor(partZ[p]);
    const rangeIndex = ranges.length;
    const start = triangleBase;
    let xmin = Infinity, xmax = -Infinity;
    let ymin = Infinity, ymax = -Infinity;
    let zmin = Infinity, zmax = -Infinity;
    for (let i = 0; i < verts.length; i += 3) {
      const x = verts[i] - center[0];
      const y = verts[i + 1] - center[1];
      const z = verts[i + 2] - center[2];
      positions.push(x, y, z);
      vertBandList.push(band);
      xmin = Math.min(xmin, x); xmax = Math.max(xmax, x);
      ymin = Math.min(ymin, y); ymax = Math.max(ymax, y);
      zmin = Math.min(zmin, z); zmax = Math.max(zmax, z);
    }
    const base = vertexBase / 3;
    for (const index of faces) indices.push(index + base);
    const count = faces.length / 3;
    for (let t = 0; t < count; t += 1) {
      triBandList.push(band);
      triRangeList.push(rangeIndex);
    }
    triangleBase += count;
    vertexBase += verts.length;
    ranges.push({
      index: rangeIndex, start, count, group, name: part.name, role: part.role, band,
      box: [xmin, xmax, ymin, ymax, zmin, zmax],
    });
  }

  const pos = new Float32Array(positions);
  const tris = new Uint32Array(indices);
  const triangles = tris.length / 3;
  const colors = new Array(triangles);
  const hoverColors = new Array(triangles);
  const selectedColors = new Array(triangles);
  const highlightColors = new Array(triangles);
  const alphas = new Float32Array(triangles);
  const centroids = new Float32Array(triangles * 3);
  const normals = new Float32Array(triangles * 3);
  const depths = new Float32Array(triangles);
  const lightA = normalize([0.45, -0.62, 0.86]);
  const lightB = normalize([-0.6, 0.55, 0.35]);

  for (const range of ranges) {
    const base = hexColor(partColor(payload, range.name));
    for (let t = range.start; t < range.start + range.count; t += 1) {
      const i0 = tris[t * 3] * 3;
      const i1 = tris[t * 3 + 1] * 3;
      const i2 = tris[t * 3 + 2] * 3;
      const ax = pos[i0], ay = pos[i0 + 1], az = pos[i0 + 2];
      const bx = pos[i1], by = pos[i1 + 1], bz = pos[i1 + 2];
      const cx = pos[i2], cy = pos[i2 + 1], cz = pos[i2 + 2];
      let nx = (by - ay) * (cz - az) - (bz - az) * (cy - ay);
      let ny = (bz - az) * (cx - ax) - (bx - ax) * (cz - az);
      let nz = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax);
      const length = Math.hypot(nx, ny, nz) || 1;
      nx /= length; ny /= length; nz /= length;
      normals[t * 3] = nx; normals[t * 3 + 1] = ny; normals[t * 3 + 2] = nz;
      centroids[t * 3] = (ax + bx + cx) / 3;
      centroids[t * 3 + 1] = (ay + by + cy) / 3;
      centroids[t * 3 + 2] = (az + bz + cz) / 3;
      const lamA = Math.abs(nx * lightA[0] + ny * lightA[1] + nz * lightA[2]);
      const lamB = Math.abs(nx * lightB[0] + ny * lightB[1] + nz * lightB[2]);
      let shade = 0.30 + 0.62 * lamA + 0.18 * lamB;
      shade = Math.max(0.22, Math.min(1.18, shade));
      const rgb = base.map((channel, index) => {
        let value = channel * shade;
        if (index === 2) value = Math.min(255, value + 6);
        return Math.max(0, Math.min(255, Math.round(value)));
      });
      colors[t] = `rgb(${rgb[0]},${rgb[1]},${rgb[2]})`;
      hoverColors[t] = tintRgb(rgb, [232, 184, 94], 0.22);
      highlightColors[t] = tintRgb(rgb, [185, 210, 240], 0.30);
      selectedColors[t] = tintRgb(rgb, [240, 192, 106], 0.46);
      alphas[t] = 1;
    }
  }

  const bandCount = Math.max(1, bands.length);
  const layerGap = Math.min(6, Math.max(0.8, (V.radius * 1.8) / Math.max(1, bandCount - 1)));
  V.mesh = {
    pos, tris, triangles, ranges, colors, hoverColors, selectedColors, highlightColors, alphas,
    centroids, normals, depths,
    vertBand: new Uint16Array(vertBandList), triBand: new Uint16Array(triBandList),
    triRange: new Uint16Array(triRangeList),
    bandZ, midBand, bandCount, layerGap,
    partCount: ranges.length, mass: payload.mass_g || 0,
  };
  const restored = previousSelection
    ? ranges.findIndex((range) => range.name === previousSelection)
    : -1;
  V.selected = restored >= 0 ? restored : null;
  V.hover = null;
  V.projection = null;
  updatePartHud();
  const info = $("#viewerInfo");
  if (info) {
    info.textContent = `${ranges.length} parts · ${triangles.toLocaleString()} triangles · ${fmt(payload.mass_g, 1)} g · ${bandCount} layers · 1 unit = 1 mm`;
  }
  if (!V.ready) {
    V.ready = true;
    fitView();
  }
  setExplodeValue(Math.round(V.explode * 100), false);
  updateLayerInfo();
}

function partColor(payload, name) {
  const part = (payload.parts || []).find((item) => item.name === name);
  return part?.color || "#b9c2cc";
}

function renderGroupToggles(tree) {
  const holder = $("#groupToggles");
  const groups = Object.keys(tree || {});
  if (!groups.length) { holder.innerHTML = ""; return; }
  holder.innerHTML = groups.map((group) => {
    const hidden = V.hidden.has(group);
    const excluded = V.isolate && !V.isolate.has(group);
    const state = hidden ? "off" : excluded ? "dim" : "";
    return `<button data-group="${esc(group)}" class="${state}"
      title="${excluded ? "hidden by the current view" : hidden ? "hidden" : "visible"}">${esc(group)}</button>`;
  }).join("");
}

function setViewFromHash() {
  if (!window.location) return;
  const name = String(window.location.hash || "").replace("#", "").toLowerCase();
  if (["three", "dial", "crown", "gears", "layers"].includes(name)) setView(name);
}

function setView(name) {
  V.isolate = null;
  if (name === "dial") {
    V.yaw = -Math.PI / 2; V.pitch = 1.535;
  } else if (name === "crown") {
    V.yaw = 0; V.pitch = 0.16;
  } else if (name === "gears") {
    V.isolate = new Set(["Movement", "Complications"]);
    V.yaw = -1.05;
    V.pitch = 1.18;
    setExplodeValue(Math.max(V.explode * 100, 85), false);
  } else if (name === "layers") {
    V.yaw = -Math.PI / 2;
    V.pitch = 0.10;
    setExplodeValue(100, false);
  } else if (name === "reset") {
    // Fit only: keep the current orientation.
  } else {
    V.yaw = -1.10; V.pitch = 1.02;
  }
  fitView();
  renderGroupToggles(V.tree);
  keepSelectionVisible();
  updateLayerInfo();
  viewerEvent("watchcad:view", { name });
  emitVisibilityState();
  viewerEvent("watchcad:explode", { value: V.explode });
  requestRender();
}

function render3d() {
  const mesh = V.mesh;
  const canvas = V.canvas;
  const ctx = V.ctx;
  if (!mesh || !canvas || !ctx) return;
  // The Three.js module owns the visible canvas when WebGL is active; skip the
  // software raster pass so both renderers do not compete for the CPU.
  if (document.body?.classList?.contains("webgl3d")) { V.ready = true; return; }
  ensureViewerSize();
  const dpr = canvas.width / Math.max(1, canvas.clientWidth);
  const width = canvas.width / dpr;
  const height = canvas.height / dpr;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);

  const cp = Math.cos(V.pitch);
  const sp = Math.sin(V.pitch);
  const cy = Math.cos(V.yaw);
  const sy = Math.sin(V.yaw);
  const cam = [V.dist * cp * cy, V.dist * cp * sy, V.dist * sp];
  const forward = normalize([-cam[0], -cam[1], -cam[2]]);
  let right = [-sy, cy, 0];
  right = normalize(right);
  const up = normalize([
    right[1] * forward[2] - right[2] * forward[1],
    right[2] * forward[0] - right[0] * forward[2],
    right[0] * forward[1] - right[1] * forward[0],
  ]);
  // screen-up must follow the camera, not the world Z at steep pitch
  const screenUp = [-up[0], -up[1], -up[2]];
  const focus = V.focus || [0, 0, 0];
  const bandShift = new Float32Array(mesh.bandCount);
  for (let i = 0; i < mesh.bandCount; i += 1) {
    bandShift[i] = (mesh.bandZ[i] - mesh.midBand) * V.explode * mesh.layerGap;
  }

  const focal = Math.min(width, height) * 1.22;
  const cx = width / 2 + V.panX;
  const cyScreen = height / 2 + V.panY;
  const pos = mesh.pos;
  const vertexCount = pos.length / 3;
  const screen = new Float32Array(vertexCount * 2);
  const vertexDepth = new Float32Array(vertexCount);

  for (let v = 0; v < vertexCount; v += 1) {
    const x = pos[v * 3] - focus[0];
    const y = pos[v * 3 + 1] - focus[1];
    const z = pos[v * 3 + 2] - focus[2] + bandShift[mesh.vertBand[v]];
    const px = x - cam[0], py = y - cam[1], pz = z - cam[2];
    const depth = px * forward[0] + py * forward[1] + pz * forward[2];
    const rx = px * right[0] + py * right[1] + pz * right[2];
    const uy = px * screenUp[0] + py * screenUp[1] + pz * screenUp[2];
    const safe = Math.max(0.15, depth);
    screen[v * 2] = cx + rx * focal / safe;
    screen[v * 2 + 1] = cyScreen + uy * focal / safe;
    vertexDepth[v] = depth;
  }
  V.projection = { screen, vertexDepth, width, height, bandShift, cam, focus };

  const order = [];
  const tris = mesh.tris;
  const normal = mesh.normals;
  for (const range of mesh.ranges) {
    if (!isRangeVisible(range)) continue;
    const shift = bandShift[range.band];
    for (let t = range.start; t < range.start + range.count; t += 1) {
      // backface cull: outward normal must point toward the camera
      const nx = normal[t * 3], ny = normal[t * 3 + 1], nz = normal[t * 3 + 2];
      const dx = cam[0] - (mesh.centroids[t * 3] - focus[0]);
      const dy = cam[1] - (mesh.centroids[t * 3 + 1] - focus[1]);
      const dz = cam[2] - (mesh.centroids[t * 3 + 2] - focus[2] + shift);
      if (nx * dx + ny * dy + nz * dz <= 0.0 && mesh.alphas[t] === 1) continue;
      const i0 = tris[t * 3], i1 = tris[t * 3 + 1], i2 = tris[t * 3 + 2];
      mesh.depths[t] = (vertexDepth[i0] + vertexDepth[i1] + vertexDepth[i2]) / 3;
      order.push(t);
    }
  }
  order.sort((a, b) => mesh.depths[b] - mesh.depths[a]);

  let alpha = 1;
  ctx.globalAlpha = 1;
  ctx.lineJoin = "round";
  for (const t of order) {
    const a = mesh.alphas[t];
    if (a !== alpha) { ctx.globalAlpha = a; alpha = a; }
    const i0 = tris[t * 3] * 2, i1 = tris[t * 3 + 1] * 2, i2 = tris[t * 3 + 2] * 2;
    if (vertexDepth[tris[t * 3]] <= 0.2 || vertexDepth[tris[t * 3 + 1]] <= 0.2
      || vertexDepth[tris[t * 3 + 2]] <= 0.2) continue;
    ctx.beginPath();
    ctx.moveTo(screen[i0], screen[i0 + 1]);
    ctx.lineTo(screen[i1], screen[i1 + 1]);
    ctx.lineTo(screen[i2], screen[i2 + 1]);
    ctx.closePath();
    const part = mesh.triRange[t];
    const selected = V.selected !== null && part === V.selected;
    const hovered = !selected && V.hover !== null && part === V.hover;
    const range = mesh.ranges[part];
    const highlighted = !selected && !hovered && range
      && V.highlightParts.has(range.name);
    const color = selected ? mesh.selectedColors[t]
      : hovered ? mesh.hoverColors[t]
        : highlighted ? mesh.highlightColors[t] : mesh.colors[t];
    ctx.fillStyle = color;
    ctx.fill();
    if (selected) {
      ctx.strokeStyle = "#f0c06a";
      ctx.lineWidth = 1.05;
      ctx.stroke();
    } else if (hovered) {
      ctx.strokeStyle = "#d9a441";
      ctx.lineWidth = 0.8;
      ctx.stroke();
    } else if (!V.fast) {
      ctx.strokeStyle = color;
      ctx.lineWidth = 0.55;
      ctx.stroke();
    }
  }
  ctx.globalAlpha = 1;
  V.fast = false;
}

function pointInTriangle(px, py, ax, ay, bx, by, cx, cy) {
  const d1 = (px - bx) * (ay - by) - (ax - bx) * (py - by);
  const d2 = (px - cx) * (by - cy) - (bx - cx) * (py - cy);
  const d3 = (px - ax) * (cy - ay) - (cx - ax) * (py - ay);
  const hasNegative = d1 < 0 || d2 < 0 || d3 < 0;
  const hasPositive = d1 > 0 || d2 > 0 || d3 > 0;
  return !(hasNegative && hasPositive);
}

function pickAt(clientX, clientY) {
  const mesh = V.mesh;
  const projection = V.projection;
  const canvas = V.canvas;
  if (!mesh || !projection || !canvas) return null;
  const rect = canvas.getBoundingClientRect();
  const x = clientX - rect.left;
  const y = clientY - rect.top;
  if (x < 0 || y < 0 || x > rect.width || y > rect.height) return null;
  const screen = projection.screen;
  const vertexDepth = projection.vertexDepth;
  const tris = mesh.tris;
  const normals = mesh.normals;
  const centroids = mesh.centroids;
  const cam = projection.cam;
  const focus = projection.focus;
  let best = null;
  let bestDepth = Infinity;

  for (const range of mesh.ranges) {
    if (!isRangeVisible(range)) continue;
    const shift = projection.bandShift[range.band];
    for (let t = range.start; t < range.start + range.count; t += 1) {
      const i0 = tris[t * 3], i1 = tris[t * 3 + 1], i2 = tris[t * 3 + 2];
      const d0 = vertexDepth[i0], d1 = vertexDepth[i1], d2 = vertexDepth[i2];
      if (d0 <= 0.2 || d1 <= 0.2 || d2 <= 0.2) continue;
      const nx = normals[t * 3], ny = normals[t * 3 + 1], nz = normals[t * 3 + 2];
      const dx = cam[0] - (centroids[t * 3] - focus[0]);
      const dy = cam[1] - (centroids[t * 3 + 1] - focus[1]);
      const dz = cam[2] - (centroids[t * 3 + 2] - focus[2] + shift);
      if (nx * dx + ny * dy + nz * dz <= 0) continue;
      const ax = screen[i0 * 2], ay = screen[i0 * 2 + 1];
      const bx = screen[i1 * 2], by = screen[i1 * 2 + 1];
      const cx = screen[i2 * 2], cy = screen[i2 * 2 + 1];
      if (x < Math.min(ax, bx, cx) || x > Math.max(ax, bx, cx)
        || y < Math.min(ay, by, cy) || y > Math.max(ay, by, cy)) continue;
      if (!pointInTriangle(x, y, ax, ay, bx, by, cx, cy)) continue;
      const depth = (d0 + d1 + d2) / 3;
      if (depth < bestDepth) {
        bestDepth = depth;
        best = { index: range.index, name: range.name, group: range.group, role: range.role, depth };
      }
    }
  }
  return best;
}

function updatePartHud() {
  const hud = $("#partHud");
  if (!hud) return;
  const range = V.selected !== null ? V.mesh?.ranges?.[V.selected] : null;
  if (!range) { hud.hidden = true; return; }
  const name = $("#partName");
  const meta = $("#partMeta");
  if (name) name.textContent = range.name;
  if (meta) {
    const box = range.box;
    const size = [box[1] - box[0], box[3] - box[2], box[5] - box[4]].map((value) => fmt(value, 1));
    meta.textContent = `${range.group} · ${size[0]} × ${size[1]} × ${size[2]} mm`;
  }
  hud.hidden = false;
}

function updateViewerCursor() {
  const canvas = V.canvas;
  if (!canvas) return;
  if (V.dragging) canvas.style.cursor = V.dragging === "pan" ? "move" : "grabbing";
  else canvas.style.cursor = V.hover !== null ? "pointer" : "grab";
}

function clearPartSelection() {
  if (V.selected === null) {
    setComponentFocus(null);
    return;
  }
  V.selected = null;
  updatePartHud();
  updateViewerCursor();
  viewerEvent("watchcad:select-part", null);
  requestRender(false);
}

function keepSelectionVisible() {
  if (V.selected === null) return;
  const range = V.mesh?.ranges?.[V.selected];
  if (range && isRangeVisible(range)) return;
  V.selected = null;
  updatePartHud();
  updateViewerCursor();
}

function selectPartAt(clientX, clientY) {
  const hit = pickAt(clientX, clientY);
  if (!hit) {
    if (V.selected !== null) clearPartSelection();
    else requestRender(false);
    return;
  }
  V.selected = hit.index;
  V.hover = hit.index;
  updatePartHud();
  updateViewerCursor();
  viewerEvent("watchcad:select-part", {
    name: hit.name, role: hit.role, group: hit.group,
  });
  requestRender(false);
}

function scheduleHover(clientX, clientY) {
  clearTimeout(V.hoverTimer);
  V.hoverTimer = setTimeout(() => {
    const hit = pickAt(clientX, clientY);
    const next = hit ? hit.index : null;
    if (next !== V.hover) {
      V.hover = next;
      updateViewerCursor();
      requestRender(false);
    }
  }, 45);
}

function tintRgb(rgb, target, amount) {
  const mix = (channel, index) => Math.round(channel + (target[index] - channel) * amount);
  return `rgb(${mix(rgb[0], 0)},${mix(rgb[1], 1)},${mix(rgb[2], 2)})`;
}

function hexColor(text) {
  const value = String(text || "#b9c2cc").replace("#", "");
  const full = value.length === 3 ? value.split("").map((c) => c + c).join("") : value;
  const number = parseInt(full.slice(0, 6), 16) || 0xb9c2cc;
  return [(number >> 16) & 255, (number >> 8) & 255, number & 255];
}

function normalize(vector) {
  const length = Math.hypot(vector[0], vector[1], vector[2]) || 1;
  return [vector[0] / length, vector[1] / length, vector[2] / length];
}

/* Read-only inspection hook for smoke tests and browser automation.
   Classic-script `const`/`let` bindings are not window properties, so expose
   getters here rather than duplicating state on the global object. */
try {
  Object.defineProperty(window, "WatchCAD", {
    value: Object.freeze({
      get ready() { return Boolean(STATE && V && V.mesh); },
      get STATE() { return STATE; },
      get SCHEMA() { return SCHEMA; },
      get CHAT() { return CHAT; },
      get AGENT() { return AGENT; },
      get DESIGN() { return DESIGN; },
      get V() { return V; },
      modes: AGENT_MODES.slice(),
    }),
  });
} catch { /* non-browser harnesses run app.js in a bare VM context */ }

boot();
