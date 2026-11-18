/* Real-browser integration tests for the WatchCAD Three.js viewer.

   Unlike tests/ui_smoke.js, this suite loads web/index.html in headless Firefox
   and executes the actual viewer3d.js module with real GLB fetches.  Firefox's
   WebDriver BiDi endpoint is driven directly with Node's built-in WebSocket,
   so no npm packages, geckodriver or fake viewer are involved.

       node tests/browser_integration.js

   Override WATCHCAD_FIREFOX, WATCHCAD_BROWSER_TIMEOUT or WATCHCAD_KEEP_OPEN=1
   for local debugging. */
const { spawn } = require("child_process");
const fs = require("fs");
const net = require("net");
const os = require("os");
const path = require("path");
const { createServer } = require("http");

const ROOT = path.resolve(__dirname, "..");
const FIREFOX = process.env.WATCHCAD_FIREFOX || "/usr/bin/firefox";
const DEFAULT_TIMEOUT = Number(process.env.WATCHCAD_BROWSER_TIMEOUT || 30000);
const WATCHED_EVENTS = [
  "watchcad:scene-changed", "watchcad:model-url", "watchcad:session-changed",
  "watchcad:viewer-model", "watchcad:viewer-stale", "watchcad:viewer-error",
];

const failures = [];
const check = (condition, message) => {
  if (condition) console.log(`  ok   ${message}`);
  else { failures.push(message); console.log(`  FAIL ${message}`); }
};
const section = (name) => console.log(`\n${name}`);
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function freePort() {
  return new Promise((resolve, reject) => {
    const probe = net.createServer();
    probe.once("error", reject);
    probe.listen(0, "127.0.0.1", () => {
      const { port } = probe.address();
      probe.close(() => resolve(port));
    });
  });
}

async function waitForHttp(url, timeout = 20000) {
  const started = Date.now();
  while (Date.now() - started < timeout) {
    try {
      const response = await fetch(url);
      if (response.ok || response.status === 404) return true;
    } catch { /* server not up yet */ }
    await delay(120);
  }
  throw new Error(`timed out waiting for ${url}`);
}

function tailStream(stream, label) {
  let text = "";
  stream.on("data", (chunk) => {
    text = (text + String(chunk)).slice(-8000);
    stream.__text = text;
  });
  return stream;
}

async function connectWebSocket(url, timeout = 20000) {
  const started = Date.now();
  let lastError;
  while (Date.now() - started < timeout) {
    try {
      return await new Promise((resolve, reject) => {
        const socket = new WebSocket(url);
        socket.onopen = () => resolve(socket);
        socket.onerror = () => reject(new Error(`cannot connect to ${url}`));
      });
    } catch (error) {
      lastError = error;
      await delay(150);
    }
  }
  throw lastError || new Error(`cannot connect to ${url}`);
}

class Bidi {
  constructor(socket) {
    this.socket = socket;
    this.nextId = 1;
    this.pending = new Map();
    socket.onmessage = (event) => {
      const message = JSON.parse(event.data);
      const entry = this.pending.get(message.id);
      if (!entry) return;
      this.pending.delete(message.id);
      clearTimeout(entry.timer);
      if (message.type === "success") entry.resolve(message);
      else entry.reject(new Error(`${message.error}: ${message.message}`));
    };
    socket.onclose = () => {
      for (const entry of this.pending.values()) {
        clearTimeout(entry.timer);
        entry.reject(new Error("WebDriver BiDi connection closed"));
      }
      this.pending.clear();
    };
  }

  send(method, params = {}) {
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`BiDi command timed out: ${method}`));
      }, 60000);
      this.pending.set(id, { resolve, reject, timer });
      this.socket.send(JSON.stringify({ id, method, params }));
    });
  }

  async session() {
    const created = await this.send("session.new", { capabilities: {} });
    this.sessionId = created.result.sessionId;
    const tree = await this.send("browsingContext.getTree", {});
    this.context = tree.result.contexts[0].context;
    return this.context;
  }

  async end() {
    if (!this.sessionId) return;
    try { await this.send("session.end", { sessionId: this.sessionId }); }
    catch { /* browser may already be gone */ }
    this.sessionId = "";
  }
}

(async () => {
  let server;
  let firefox;
  let bidi;
  let scratch;
  let context;

  const evaluate = async (expression, awaitPromise = false) => {
    const response = await bidi.send("script.evaluate", {
      expression,
      target: { context },
      awaitPromise,
      resultOwnership: "root",
    });
    if (response.result.type === "exception") {
      const details = response.result.exceptionDetails || {};
      throw new Error(details.text || details.exception?.description
        || "browser evaluation failed");
    }
    return response.result.result?.value;
  };
  const evalJson = async (expression, awaitPromise = false) => {
    const raw = await evaluate(expression, awaitPromise);
    return raw === undefined || raw === null ? null : JSON.parse(raw);
  };
  const waitForBoolean = async (predicate, label, timeout = DEFAULT_TIMEOUT) => {
    const expression = `new Promise((resolve, reject) => {
      const started = Date.now();
      const tick = () => {
        let ok = false;
        try { ok = Boolean(${predicate}); } catch { ok = false; }
        if (ok) return resolve("true");
        if (Date.now() - started > ${timeout}) {
          return reject(new Error(${JSON.stringify(`timed out waiting for ${label}`)}));
        }
        setTimeout(tick, 50);
      };
      tick();
    })`;
    await evaluate(expression, true);
  };
  const model = () => evalJson("JSON.stringify(window.WatchCAD3D.modelSnapshot())");
  const resources = () => evalJson("JSON.stringify(window.WatchCAD3D.resourceStats())");
  const appState = () => evalJson(`JSON.stringify({
    revision: window.WatchCAD.STATE.revision,
    checksum: window.WatchCAD.STATE.spec.data.derived.checksum,
    session: window.WatchCAD.AGENT.sessionId,
  })`);
  const waitForModel = async (predicate, label, timeout = DEFAULT_TIMEOUT) => {
    await waitForBoolean(
      `(() => { const m = window.WatchCAD3D?.modelSnapshot?.(); return m && (${predicate}); })()`,
      label, timeout);
    return model();
  };
  const events = () => evalJson("JSON.stringify(window.__wcEvents || [])");
  const clearEvents = async () => {
    await evaluate("window.__wcEvents = []; 'ok'");
  };
  const waitForEvent = async (type, timeout = DEFAULT_TIMEOUT) => {
    await waitForBoolean(
      `Boolean((window.__wcEvents || []).find((entry) => entry.type === ${JSON.stringify(type)}))`,
      `event ${type}`, timeout);
    return events();
  };
  const changeField = async (field, value) => {
    await evaluate(`window.setField(${JSON.stringify(field)}, ${JSON.stringify(value)})`, true);
    return waitForModel(
      `m.revision === window.WatchCAD.STATE.revision
       && m.checksum === window.WatchCAD.STATE.spec.data.derived.checksum
       && m.activeLoads === 0`,
      `set ${field}=${value}`);
  };
  const part = (snapshot, name) =>
    (snapshot?.parts || []).find((item) => item.name === name);

  try {
    scratch = fs.mkdtempSync(path.join("/tmp/opencode", "watchcad-browser-"));
    const serverPort = await freePort();
    const browserPort = await freePort();
    server = spawn("python3", ["-m", "watchcad", "serve",
      "--host", "127.0.0.1", "--port", String(serverPort)], {
      cwd: ROOT,
      env: { ...process.env, WATCHCAD_DB: path.join(scratch, "browser.sqlite3") },
      stdio: ["ignore", "pipe", "pipe"],
    });
    tailStream(server.stdout, "server");
    tailStream(server.stderr, "server");
    await waitForHttp(`http://127.0.0.1:${serverPort}/api/state`);

    fs.writeFileSync(path.join(scratch, "user.js"), [
      'user_pref("webgl.force-enabled", true);',
      'user_pref("webgl.disabled", false);',
      'user_pref("webgl.enable-webgl2", true);',
      'user_pref("gfx.webrender.all", true);',
      'user_pref("layers.acceleration.disabled", true);',
    ].join("\n"));
    firefox = spawn(FIREFOX, ["--no-remote",
      "--remote-debugging-port", String(browserPort),
      "--profile", scratch, "about:blank"], {
      cwd: ROOT,
      env: { ...process.env, MOZ_HEADLESS: "1" },
      stdio: ["ignore", "pipe", "pipe"],
    });
    tailStream(firefox.stdout, "firefox");
    tailStream(firefox.stderr, "firefox");
    await waitForHttp(`http://127.0.0.1:${browserPort}/`);

    const socket = await connectWebSocket(`ws://127.0.0.1:${browserPort}/session`);
    bidi = new Bidi(socket);
    context = await bidi.session();
    const base = `http://127.0.0.1:${serverPort}`;
    await bidi.send("browsingContext.navigate",
      { context, url: `${base}/index.html`, wait: "complete" });

    section("real Three.js viewer boot");
    await waitForBoolean(
      "Boolean(window.WatchCAD?.ready && window.WatchCAD3D?.available)",
      "app and 3D viewer boot", DEFAULT_TIMEOUT);
    const capabilities = await evalJson(`JSON.stringify({
      available: window.WatchCAD3D.available,
      sceneType: window.WatchCAD3D.scene?.type || "",
      webgl2: Boolean(document.getElementById("viewer3d")?.getContext("webgl2")),
      vendor: window.WatchCAD3D.scene ? "three" : "",
    })`);
    check(capabilities.available && capabilities.sceneType === "Scene",
      "actual Three.js module is running, not the fake smoke viewer");
    check(capabilities.webgl2, "headless Firefox exposes a real WebGL 2 context");

    const initial = await waitForModel(
      "m.ready && m.revision > 0 && m.parts.length > 0 && m.activeLoads === 0",
      "initial GLB load");
    check(initial.parts.length > 20, `real GLB produced ${initial.parts.length} meshes`);
    check(initial.embeddedIdentity.revision === initial.revision
      && initial.embeddedIdentity.session === initial.session
      && initial.embeddedIdentity.checksum === initial.checksum,
    "GLB asset extras match the installed model identity");
    await evaluate(`(() => {
      window.__wcEvents = [];
      for (const type of ${JSON.stringify(WATCHED_EVENTS)}) {
        window.addEventListener(type, (event) => {
          let detail = null;
          try { detail = event.detail == null ? null : JSON.parse(JSON.stringify(event.detail)); }
          catch { detail = null; }
          window.__wcEvents.push({ type, detail, at: Date.now() });
        });
      }
      return "ok";
    })()`);

    section("1. case diameter changes real geometry");
    const diameterBefore = await model();
    const diameterAfter = await changeField("case.diameter_mm", 43.5);
    const caseBefore = part(diameterBefore, "case_mid");
    const caseAfter = part(diameterAfter, "case_mid");
    check(diameterAfter.revision > diameterBefore.revision
      && diameterAfter.checksum !== diameterBefore.checksum,
    "diameter edit installs a newer committed revision");
    check(Boolean(caseBefore && caseAfter)
      && Math.abs(caseAfter.bounds.size[0] - caseBefore.bounds.size[0]) > 0.5,
    "case_mid bounds follow the new diameter");
    check(caseAfter.fingerprint !== caseBefore.fingerprint,
      "case_mid vertex data changes in the loaded GLB");

    section("2. case thickness moves the bezel mesh");
    const bezelBefore = await model();
    const bezelAfter = await changeField("case.thickness_mm", 13.5);
    const oldBezel = part(bezelBefore, "bezel");
    const newBezel = part(bezelAfter, "bezel");
    check(Boolean(oldBezel && newBezel)
      && Math.abs(newBezel.bounds.max[2] - oldBezel.bounds.max[2]) > 0.01,
    `bezel Z bounds move with case.thickness_mm (${JSON.stringify(oldBezel?.bounds?.max)} → ${JSON.stringify(newBezel?.bounds?.max)}; checksum ${bezelBefore.checksum} → ${bezelAfter.checksum})`);

    section("3. dial colour changes the actual GLB material");
    const dialBefore = await model();
    const dialAfter = await changeField("dial.color", "blue");
    check(part(dialBefore, "dial")?.color !== part(dialAfter, "dial")?.color,
      `dial material colour changes from ${part(dialBefore, "dial")?.color} to ${part(dialAfter, "dial")?.color}`);

    section("4. hand style changes hand meshes");
    const handsBefore = await model();
    const handsAfter = await changeField("hands.style", "dauphine");
    const hourBefore = part(handsBefore, "hour_hand");
    const hourAfter = part(handsAfter, "hour_hand");
    check(Boolean(hourBefore && hourAfter)
      && (hourBefore.fingerprint !== hourAfter.fingerprint
        || hourBefore.vertices !== hourAfter.vertices),
    "hour_hand geometry changes with hands.style");

    section("5. rapid edits coalesce to the newest revision");
    const setupDelay = await evaluate(`(() => {
      const realFetch = window.fetch.bind(window);
      window.__wcRestoreFetch = () => { window.fetch = realFetch; window.__wcDelay = 0; };
      window.__wcDelay = 0;
      window.fetch = (input, init) => {
        const url = typeof input === "string" ? input : (input?.url || "");
        if (url.includes("/api/model.glb") && window.__wcDelay > 0) {
          const ms = window.__wcDelay;
          window.__wcDelay = 0;
          return new Promise((resolve) => setTimeout(() => resolve(realFetch(input, init)), ms));
        }
        return realFetch(input, init);
      };
      window.__wcDelay = 1200;
      return "ok";
    })()`);
    check(setupDelay === "ok", "instrumented the first real GLB request with a delay");
    try {
      await clearEvents();
      await evaluate('window.setField("case.diameter_mm", 41)', true);
      const firstRevision = await evalJson("JSON.stringify(window.WatchCAD.STATE.revision)");
      await waitForModel(
        `m.activeLoads === 1 && m.revision < ${firstRevision}`,
        "first GLB request still in flight");
      await evaluate('window.setField("case.diameter_mm", 45)', true);
      const newestRevision = await evalJson("JSON.stringify(window.WatchCAD.STATE.revision)");
      const finalRapid = await waitForModel(
        `m.revision === ${newestRevision} && m.activeLoads === 0 && m.pendingLoads === 0`,
        "newest GLB installed");
      const rapidModelEvents = (await events()).filter(
        (entry) => entry.type === "watchcad:viewer-model");
      check(newestRevision > firstRevision, "both rapid edits committed distinct revisions");
      check(finalRapid.revision === newestRevision
        && finalRapid.expected.revision === newestRevision,
      "only the newest requested revision is installed");
      check(rapidModelEvents.length === 1
        && rapidModelEvents[0].detail.revision === newestRevision,
      "the superseded request never acknowledges a model");
    } finally {
      await evaluate("window.__wcRestoreFetch()");
    }

    section("6. switching sessions with different generations");
    await evaluate("window.newChat()", true);
    const sessionOne = await waitForModel(
      `m.ready && m.session === window.WatchCAD.AGENT.sessionId
       && m.checksum === window.WatchCAD.STATE.spec.data.derived.checksum`,
      "first conversation session");
    const firstSession = await evalJson(`JSON.stringify({
      id: window.WatchCAD.CHAT.conversationId,
      session: window.WatchCAD.AGENT.sessionId,
      checksum: window.WatchCAD.STATE.spec.data.derived.checksum,
    })`);
    const colouredSession = await changeField("dial.color", "green");
    await evaluate("window.newChat()", true);
    await waitForModel(
      `m.ready && m.session === window.WatchCAD.AGENT.sessionId
       && m.checksum === window.WatchCAD.STATE.spec.data.derived.checksum`,
      "second conversation session");
    const secondSession = await changeField("case.material", "titanium");
    check(secondSession.generation > colouredSession.generation,
      `second session advanced generation to ${secondSession.generation}`);
    await clearEvents();
    await evaluate(`window.openConversation(${JSON.stringify(firstSession.id)})`, true);
    const returned = await waitForModel(
      `m.session === ${JSON.stringify(firstSession.session)}
       && m.checksum === ${JSON.stringify(colouredSession.checksum)}
       && m.activeLoads === 0`,
      "return to the first session");
    const sessionEvents = (await events()).filter(
      (entry) => entry.type === "watchcad:session-changed");
    check(returned.checksum === colouredSession.checksum,
      "returning to an existing session loads its own design");
    check(returned.generation < secondSession.generation,
      `a lower generation (${returned.generation} < ${secondSession.generation}) from another session is accepted`);
    check(sessionEvents.length > 0, "session switch invalidates viewer state via an event");

    section("7. live design to optimisation preview");
    await evaluate("window.runOptimisation()", true);
    await waitForBoolean(
      "Boolean(window.WatchCAD.DESIGN.optimisation?.candidates?.length)",
      "optimisation candidates");
    const candidate = await evalJson(`JSON.stringify(
      window.WatchCAD.DESIGN.optimisation.candidates
        .find((item) => item.status === "ok" && item.changes?.length) || null)`);
    check(Boolean(candidate), "optimisation produced a previewable candidate");
    if (candidate) {
      const liveBeforePreview = await model();
      await clearEvents();
      await evaluate(`window.previewCandidate(${JSON.stringify(candidate.id)})`, true);
      const preview = await waitForModel(
        `m.preview === true
         && m.previewId === window.WatchCAD.DESIGN.previewId
         && m.revision === ${liveBeforePreview.revision}`,
        "optimisation preview model");
      const previewUrlEvent = (await events()).find(
        (entry) => entry.type === "watchcad:model-url"
          && String(entry.detail?.url || "").includes("preview="));
      check(preview.checksum !== liveBeforePreview.checksum
        && preview.checksum === previewUrlEvent?.detail?.checksum,
      "preview has its own checksum and keeps the base revision");
      check(preview.embeddedIdentity.preview === true
        && preview.embeddedIdentity.previewId === preview.previewId,
      "preview GLB is isolated by its preview token");

      section("8. clearing the preview restores the live model");
      await evaluate("window.clearCandidatePreview()");
      const restored = await waitForModel(
        `m.preview === false && m.previewId === ""
         && m.checksum === ${JSON.stringify(liveBeforePreview.checksum)}
         && m.revision === ${liveBeforePreview.revision}`,
        "live model after clearing preview");
      check(JSON.stringify(restored.parts) === JSON.stringify(liveBeforePreview.parts),
        "clearing the preview reloads the original live geometry");
    }

    section("9. failed GLB request preserves the last good model");
    await evaluate(`(() => {
      const realFetch = window.fetch.bind(window);
      window.__wcFailRestore = () => { window.fetch = realFetch; };
      window.__wcFailOnce = true;
      window.fetch = (input, init) => {
        const url = typeof input === "string" ? input : (input?.url || "");
        if (url.includes("/api/model.glb") && window.__wcFailOnce) {
          window.__wcFailOnce = false;
          return Promise.resolve(new Response("", { status: 503 }));
        }
        return realFetch(input, init);
      };
      return "ok";
    })()`);
    try {
      const failedBefore = await model();
      await clearEvents();
      await evaluate('window.setField("case.diameter_mm", 41)', true);
      await waitForEvent("watchcad:viewer-error");
      await delay(250);
      const failedAfter = await model();
      const afterEvents = await events();
      check(failedAfter.revision === failedBefore.revision
        && failedAfter.checksum === failedBefore.checksum,
      "a failed newer load keeps the previous valid identity visible");
      check(JSON.stringify(failedAfter.parts) === JSON.stringify(failedBefore.parts),
      "previous geometry remains installed after the failed GLB request");
      check(afterEvents.some((entry) => entry.type === "watchcad:viewer-error")
        && !afterEvents.some((entry) => entry.type === "watchcad:viewer-model"),
      "failure is reported as an error, never as a current model");
      const failedAppState = await appState();
      check(failedAppState.revision > failedAfter.revision,
      "the committed design revision is newer than the acknowledged model");
    } finally {
      await evaluate("window.__wcFailRestore()");
      await evaluate("window.WatchCAD3D.load()");
      await waitForModel(
        `m.revision === window.WatchCAD.STATE.revision && m.activeLoads === 0`,
        "resync after failed GLB");
    }

    section("10. undo and redo restore rendered geometry");
    // Undo commits a new revision (and a new meta.version), so checksums are
    // not expected to match the older snapshot.  The geometry fingerprint is
    // the invariant that proves the restored design reached the viewport.
    const undoOne = await changeField("dial.color", "blue");
    const beforeUndo = await changeField("hands.style", "leaf");
    const undoFingerprint = part(undoOne, "hour_hand")?.fingerprint;
    const redoFingerprint = part(beforeUndo, "hour_hand")?.fingerprint;
    check(Boolean(undoFingerprint && redoFingerprint)
      && undoFingerprint !== redoFingerprint,
    "the two edits produced distinct rendered hand geometry");
    await clearEvents();
    await evaluate("window.undoTurn()", true);
    const undone = await waitForModel(
      `m.parts.find((item) => item.name === 'hour_hand')?.fingerprint
         === ${JSON.stringify(undoFingerprint)} && m.activeLoads === 0`,
      "undo restores the previous design");
    check(undone.revision > beforeUndo.revision,
      "undo is installed as a new committed model revision");
    await evaluate("window.redoTurn()", true);
    const redone = await waitForModel(
      `m.parts.find((item) => item.name === 'hour_hand')?.fingerprint
         === ${JSON.stringify(redoFingerprint)} && m.activeLoads === 0`,
      "redo restores the newer design");
    check(redone.revision > undone.revision,
      "redo is installed as a new committed model revision");

    section("resource disposal, listener stability and idle rendering");
    const perfBefore = await resources();
    for (let index = 0; index < 6; index += 1) {
      await changeField("case.diameter_mm", 40 + (index % 5));
    }
    const perfAfter = await resources();
    check(perfAfter.disposedGeometries > perfBefore.disposedGeometries,
      `old geometries disposed (${perfBefore.disposedGeometries} → ${perfAfter.disposedGeometries})`);
    check(perfAfter.disposedMaterials > perfBefore.disposedMaterials,
      `old materials disposed (${perfBefore.disposedMaterials} → ${perfAfter.disposedMaterials})`);
    check(perfAfter.textureCache === perfBefore.textureCache
      && perfAfter.cachedTexturesReferenced > 0,
    "shared texture cache survives model replacement while still referenced");
    check(perfAfter.geometries === perfBefore.geometries,
      "live scene resource count stays stable across six replacements");
    check(JSON.stringify(perfAfter.listeners) === JSON.stringify(perfBefore.listeners),
      "event listeners do not accumulate across model loads");

    await delay(500);
    const idleBefore = await resources();
    await delay(800);
    const idleAfter = await resources();
    check(idleBefore.frames === idleAfter.frames
      && idleBefore.renders === idleAfter.renders,
    "idle viewer schedules no frames or renders");

    const afterUnload = await evalJson(`JSON.stringify((() => {
      window.dispatchEvent(new Event("pagehide"));
      return window.WatchCAD3D.resourceStats();
    })())`);
    check(afterUnload.disposed && afterUnload.textureCache === 0
      && afterUnload.disposedTextures > 0 && !afterUnload.framePending,
    "pagehide disposes textures, WebGL resources and pending frames");
    check(Object.keys(afterUnload.listeners).length === 0,
      "pagehide removes every viewer listener");
  } catch (error) {
    failures.push(`test harness: ${error.message}`);
    console.error(`\nHARNESS ERROR: ${error.stack || error.message}`);
    if (server?.stderr?.__text) console.error(`\nserver log:\n${server.stderr.__text}`);
    if (firefox?.stderr?.__text) console.error(`\nfirefox log:\n${firefox.stderr.__text}`);
  } finally {
    if (bidi) {
      await bidi.end();
      try { bidi.socket.close(); } catch { /* already closed */ }
    }
    if (firefox && !firefox.killed) firefox.kill("SIGTERM");
    if (server && !server.killed) server.kill("SIGTERM");
    if (scratch && process.env.WATCHCAD_KEEP_OPEN !== "1") {
      fs.rmSync(scratch, { recursive: true, force: true });
    }
    console.log(`\n${failures.length ? `${failures.length} failure(s)` : "all real-browser checks passed"}`);
    if (failures.length) process.exitCode = 1;
  }
})();
