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
  capabilities: {},
  capabilityKey: "",
  providerKey: false,
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

function withSession(url) {
  const separator = String(url).includes("?") ? "&" : "?";
  return `${url}${separator}session=${encodeURIComponent(currentSession())}`;
}

async function api(path, body) {
  const options = body === undefined ? { headers: {} } : {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
  options.headers = options.headers || {};
  options.headers["X-WatchCAD-Session"] = currentSession();
  const response = await fetch(path, options);
  let data = {};
  try { data = await response.json(); } catch { /* empty response */ }
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
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
  $("#btnHistory").addEventListener("click", toggleAgentHistory);
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
  $("#btnVariations").addEventListener("click", generateVariations);
  $("#btnDesignPrompt").addEventListener("click", describeFromReference);
  $("#btnDesignRef").addEventListener("click", () => $("#designReference").click());
  $("#designReference").addEventListener("change", (event) => {
    attachImages(event);
    renderDesignReferences();
  });
  $("#designPanel").addEventListener("change", onDesignChange);
  $("#btnOptimise").addEventListener("click", runOptimisation);
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
  document.addEventListener("click", onGlobalClick);
  setupViewer();
  restorePanels();
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
    const [schema, state] = await Promise.all([api("/api/schema"), api("/api/state")]);
    SCHEMA = schema;
    STATE = state;
    IDEAS = Object.fromEntries((schema.ideas || []).map((item) => [item.id, item.prompt]));
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
}

function applyState(state, options = {}) {
  if (!state || !state.spec) return;
  const previous = STATE && STATE.spec && STATE.spec.data.derived
    ? STATE.spec.data.derived.checksum : "";
  STATE = state;
  if (state.session) AGENT.sessionId = state.session.session_id || AGENT.sessionId;
  const checksum = state.spec?.data?.derived?.checksum || "";
  renderHeader();
  renderFacts();
  renderChecks();
  renderPackage();
  if (options.spec === false) syncFormValues();
  else renderSpec();
  renderDesignPanel();
  renderOptimisation();
  if (options.scene !== false && checksum !== previous) scheduleScene();
  renderSessionChip();
}

/* ---------------------------------------------------------- workspaces */
function setWorkspace(name) {
  const next = ["design", "engineering", "optimise"].includes(name) ? name : "design";
  if (document.body?.dataset) document.body.dataset.workspace = next;
  $$("#workspaces button").forEach((button) =>
    button.classList.toggle("active", button.dataset.workspace === next));
  try { window.localStorage?.setItem("watchcad.workspace", next); } catch { /* ignore */ }
  if (next === "design") renderDesignPanel();
  if (next === "optimise") renderOptimisation();
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

function renderOptimisation() {
  const holder = $("#candidateList");
  const status = $("#optimiseStatus");
  if (!holder || !status) return;
  const result = DESIGN.optimisation;
  const current = STATE?.spec?.data?.derived?.checksum || "";
  if (!result) {
    holder.innerHTML = "";
    if (!DESIGN.optimiseBusy) {
      status.textContent = "Not run yet. The current design is the baseline.";
    }
    return;
  }
  const stale = Boolean(result.baseline_checksum && current
    && result.baseline_checksum !== current);
  const candidates = result.candidates || [];
  const winner = candidates.find((item) => item.id === result.winner);
  status.innerHTML = `${stale
    ? "<b>Design changed since this run.</b> Rerun to compare against the current spec. "
    : ""}${candidates.length} candidates · objective <b>${esc(result.objective)}</b> · winner <b>${esc(winner?.label || "none")}</b>`
    + `<br><span class="provenance">calculated geometry · estimated cost · no physical verification</span>`;
  holder.innerHTML = candidates.map((candidate) => {
    const m = candidate.metrics || {};
    const isWinner = candidate.id === result.winner;
    const disabled = stale || !candidate.changes?.length || candidate.status !== "ok";
    return `<article class="candidate ${isWinner ? "winner" : ""} ${candidate.status !== "ok" ? "failed" : ""}">
      <div class="candidate-head">
        <b>${esc(candidate.label || candidate.id)}</b>
        <span class="candidate-rank">#${esc(candidate.rank ?? "—")}${isWinner ? " · best" : ""}</span>
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
        <span class="provenance ${candidate.status !== "ok" ? "failed" : candidate.measurable ? "calculated" : "estimated"}">${candidate.status !== "ok"
          ? "solver failed"
          : candidate.measurable ? "change measured" : "visual change only"}</span>
        <button class="btn small" data-apply-candidate="${esc(candidate.id)}"
          ${disabled ? "disabled" : ""} title="${stale ? "Rerun after the design changed" : "Apply through solver + verifier"}">Apply</button>
      </div>
      ${candidate.error ? `<p class="provenance failed">${esc(candidate.error)}</p>` : ""}
    </article>`;
  }).join("");
}

async function runOptimisation() {
  if (DESIGN.optimiseBusy) return;
  const objective = $("#optimiseObjective")?.value || "weight";
  const budget = Number($("#optimiseBudget")?.value || 9);
  const token = ++DESIGN.optimiseToken;
  DESIGN.optimiseBusy = true;
  const status = $("#optimiseStatus");
  if (status) status.textContent = `Solving and verifying ${budget} candidates…`;
  setLoading(true, "solving optimisation candidates");
  try {
    const result = await api("/api/optimise", { objective, budget });
    if (token !== DESIGN.optimiseToken) return;
    DESIGN.optimisation = result;
    renderOptimisation();
    const winner = (result.candidates || []).find((item) => item.id === result.winner);
    toast(winner ? `best candidate: ${winner.label}` : "optimisation returned no candidates");
  } catch (error) {
    if (status) status.textContent = `Optimisation failed: ${error.message}`;
    toast(error.message, true);
  } finally {
    DESIGN.optimiseBusy = false;
    setLoading(false);
  }
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
    const failed = Number(payload.verification?.fail ?? payload.summary?.fail ?? 0);
    toast(failed
      ? `applied with ${failed} failing check(s); inspect Engineering Studio`
      : `applied ${candidate.label} through the validated pipeline`);
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
  if (payload.snapshot) applyState(payload.snapshot);
  if (payload.conversation) {
    CHAT.conversationId = payload.conversation.id || CHAT.conversationId;
    rememberConversation(payload.conversation);
  }
  if (payload.session?.session_id) AGENT.sessionId = payload.session.session_id;
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
  if (result.session) AGENT.sessionId = result.session.session_id || AGENT.sessionId;
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
  panel.hidden = clipped;
  if (rail) rail.hidden = !clipped;
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

function onGlobalClick(event) {
  const workspace = event.target.closest("[data-workspace]");
  if (workspace) { setWorkspace(workspace.dataset.workspace); return; }
  const dialSwatch = event.target.closest("[data-dial-color]");
  if (dialSwatch) { setField("dial.color", dialSwatch.dataset.dialColor); return; }
  const candidate = event.target.closest("[data-apply-candidate]");
  if (candidate) { applyCandidate(candidate.dataset.applyCandidate); return; }
  const provider = event.target.closest("[data-provider]");
  if (provider) { selectProvider(provider.dataset.provider); return; }
  if (event.target.closest("[data-close-provider]")) { closeProviders(); return; }
  const clearPart = event.target.closest("[data-part-clear]");
  if (clearPart) { clearPartSelection(); return; }
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
    viewerEvent("watchcad:visibility", {
      hidden: [...V.hidden],
      isolate: V.isolate ? [...V.isolate] : null,
    });
    requestRender();
  }
}

function switchTab(name) {
  activeTab = name;
  $$(".tabs button").forEach((button) =>
    button.classList.toggle("active", button.dataset.tab === name));
  $$(".tab-panel").forEach((panel) =>
    panel.classList.toggle("active", panel.id === `tab-${name}`));
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

function bindViewerBridge() {
  if (typeof window?.addEventListener !== "function") return;
  window.addEventListener("watchcad:select-part", (event) => {
    const detail = event.detail;
    if (!detail || !detail.name) { clearPartSelection(); return; }
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
  window.addEventListener("watchcad:viewer-model", () => {
    // A rebuilt GLB resets the module's local explode/visibility state, so
    // replay the app's authoritative viewer state.
    viewerEvent("watchcad:visibility", {
      hidden: [...V.hidden],
      isolate: V.isolate ? [...V.isolate] : null,
    });
    viewerEvent("watchcad:explode", { value: V.explode });
  });
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
  tree: {},
  ready: false,
  hidden: new Set(),
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

function visibleBounds() {
  const mesh = V.mesh;
  if (!mesh) return null;
  const min = [Infinity, Infinity, Infinity];
  const max = [-Infinity, -Infinity, -Infinity];
  let any = false;
  for (const range of mesh.ranges) {
    if (!isVisible(range.group) || !range.box) continue;
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
    if (!isVisible(range.group)) continue;
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
  try {
    if (initial) setLoading(true, "loading the mesh");
    const payload = await api("/api/scene");
    if (token !== sceneToken) return;
    lastSceneSig = sceneSignature(STATE);
    prepareMesh(payload);
    renderGroupToggles(payload.tree || {});
    viewerEvent("watchcad:scene-changed", { checksum: sceneSignature(STATE) });
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
      selectedColors[t] = tintRgb(rgb, [240, 192, 106], 0.46);
      alphas[t] = 1;
    }
  }

  const bandCount = Math.max(1, bands.length);
  const layerGap = Math.min(6, Math.max(0.8, (V.radius * 1.8) / Math.max(1, bandCount - 1)));
  V.mesh = {
    pos, tris, triangles, ranges, colors, hoverColors, selectedColors, alphas,
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
  viewerEvent("watchcad:visibility", {
    hidden: [...V.hidden],
    isolate: V.isolate ? [...V.isolate] : null,
  });
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
    if (!isVisible(range.group)) continue;
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
    const color = selected ? mesh.selectedColors[t] : hovered ? mesh.hoverColors[t] : mesh.colors[t];
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
    if (!isVisible(range.group)) continue;
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
  if (V.selected === null) return;
  V.selected = null;
  updatePartHud();
  updateViewerCursor();
  viewerEvent("watchcad:select-part", null);
  requestRender(false);
}

function keepSelectionVisible() {
  if (V.selected === null) return;
  const range = V.mesh?.ranges?.[V.selected];
  if (range && isVisible(range.group)) return;
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
      get V() { return V; },
      modes: AGENT_MODES.slice(),
    }),
  });
} catch { /* non-browser harnesses run app.js in a bare VM context */ }

boot();
