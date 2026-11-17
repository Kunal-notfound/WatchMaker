# watchcad

Turn a sentence into a complete, parametric watch design — then inspect the
spec, the geometry, the checks and the production package.

```text
"A 42 mm steel dive watch, 300 m water resistance, black matte dial,
 applied lume indices, baton hands, sapphire crystal, screw-down crown, date."
        │
        ▼
  spec (JSON)  ──►  deterministic solver  ──►  mesh / drawings / BOM / reports
  single source      no LLM writes geometry         one build directory
```

`watchcad` implements the project brief in `Watch_CAD_Project_Spec.docx` as a
local-first studio: a Python standard-library engine with no third-party
packages, plus a browser UI served from the same process. An LLM is optional
and can only propose spec patches; every dimension, tooth count, stack height
and mesh vertex comes from deterministic code.

---

## Quick start

Requirements: Python 3.11+ and a browser. Nothing to install.

```bash
cd watch_proj

# 1. Studio: copilot chat, number fields, exploded 3D views, layered checks
python3 -m watchcad serve --open          # http://127.0.0.1:8765

# 2. Or stay on the command line
python3 -m watchcad prompt "38 mm dress watch, manual wind, no date" -o dress.json
python3 -m watchcad show dress.json
python3 -m watchcad verify dress.json
python3 -m watchcad build dress.json -o out/dress
python3 -m watchcad export dress.json --format pdf -o out/dress.pdf
```

Run the test suite:

```bash
python3 -m unittest discover -s tests -v     # 161 tests, ~54 s

# browser smoke test (Node 18+, no npm packages)
python3 -m watchcad serve --port 8766 &
node tests/ui_smoke.js                        # boot, chat, undo, focus/critic, movements, Pareto lab, preview/rollback
```

Optional editable install:

```bash
python3 -m pip install -e .                  # exposes the `watchcad` command
```

Both entry points are equivalent: `python3 -m watchcad …` and `watchcad …`.

### Built-in design briefs

`python3 -m watchcad presets` prints the prompts behind the seven shipped
examples. They all solve with **0 failures**:

| id | example | notable checks |
|---|---|---|
| `diver` | Abyss 42 | dive bezel, 300 m, screw-down crown, date |
| `dress` | Meridian 38 | manual wind, 48 h reserve, no date |
| `field` | Surveyor 38 | bead-blasted case, matte dial, lume |
| `pilot` | Navigator 43 | large crown, high legibility |
| `chronograph` | Circuit 40 | two pushers, chrono counters, panda dial |
| `moonphase` | Luna 40 | moon aperture, annual calendar |
| `quartz` | Current 36 | battery path, no gear train |

The generated specs are in [`examples/`](examples/).

### The studio

- **Three workspaces, one specification.** The top bar switches between
  **Design Studio**, **Engineering Studio** and **Optimisation Lab**. The
  central viewport never moves: every workspace edits the same solved spec and
  the same Three.js preview. Design Studio is the default, visual surface;
  Engineering Studio contains the full CAD tree, checks and package tools.
  The **Specs** and **AI agent** edge tabs stay docked beside their sidebars
  and slide them in and out smoothly in both directions: click to hide, click
  again to show.
- **Three.js PBR preview.** `/api/model.glb` is a binary glTF export of the
  solved assembly with UVs, vertex normals and PBR material extras. The browser
  renders it with physically based materials, image-based lighting from a
  procedural room environment, a directional shadow and OrbitControls. A local
  copy of Three.js 0.160 is vendored under `web/vendor/`, so the studio does not
  depend on a CDN. If WebGL is unavailable the original software canvas stays
  active as a complete fallback.
- **Render quality presets.** Low, Medium and High change tessellation, shadow
  size, pixel ratio and procedural texture size; scenes are cached per quality
  and textures are reused by the viewer. Geometry dimensions and verification
  are identical across presets — quality is a performance hint, not an
  engineering-accuracy setting.
- **Conversational assistant.** The right panel is a chat-first assistant, not a
  one-shot prompt box. Most messages are answered directly: it can explain a
  material, compare movements, research a standard, inspect the current design
  or ask a clarifying question. When you ask for a design change it uses the
  same validated tools as the agent, with streaming Markdown, tables, code
  blocks and source links. Press Enter to send, Shift+Enter for a newline.
- **Persistent conversations.** Chats are stored in SQLite with titles,
  messages, tool calls, design references and summaries. The **Chats** button
  opens a searchable history sidebar where you can start a new chat, reopen,
  rename or delete one. A new chat clones the design you are looking at and then
  diverges independently. Reloading the page restores the selected transcript
  and its design session.
- **Message actions and attachments.** Hover a message to copy, edit or delete
  it; regenerate the last answer in place. The image button stages up to four
  reference photos for the turn. A vision-capable model may discuss style and
  proportions, but the assistant never invents exact measurements from a photo.
- **Model settings and bring-your-own-key.** The **AI provider** dialog offers
  Gemini, Cloudflare Workers AI, Groq, OpenRouter and a generic
  OpenAI-compatible endpoint. It tests a real connection, reports streaming,
  tool, JSON and image capability, and can save, update or remove the key.
  Endpoint, provider and model are non-secret and persist in
  `out/settings.json`. The key itself lives only in server process memory,
  keyed by browser session: it is never written to settings, `localStorage`,
  logs, frontend bundles or API responses, and a server restart forgets it.
  Environment keys still work for headless use. An injected `LLM_CONFIG`
  takes precedence over both, and a session key overrides the environment key
  for that session. A persisted endpoint without a key does not auto-enable the
  model toggle, so the deterministic engine stays in charge until a key is
  added.
- **Agent controls.** The copilot header also exposes an autonomy selector
  (suggest / approval / safe / autonomous), Stop, Undo, Redo and History. Runs
  stream a tool-by-tool timeline with iteration numbers; the contextual result
  shows a spec diff, the verification before/after and any approval or variant
  cards. Tool arguments and raw results expand per step. The session chip
  identifies the browser's design session.
- **Collapsible sidebars.** The specs tree sits on the left and the copilot on
  the right. Each has a close button; the 3D viewport expands into the freed
  space and a slim rail stays at the edge to reopen it. The open/closed choice
  is remembered in `localStorage`.
- **Number fields.** Numeric values use a plain number input plus black
  up/down steppers. There are no range sliders in the spec editor. Keyboard
  entry, lock buttons, provenance tags and validation all still apply.
- **Exploded viewer.** Drag to orbit, wheel to zoom, shift-drag to pan. In the
  Three.js view the same controls drive a real perspective camera; the classic
  software viewport keeps cursor-anchored zoom and rigid z-band explosion as a
  no-WebGL fallback. **Explode** separates case, dial, hands, bridges and gear
  train without stretching meshes. Every part is individually clickable in
  both renderers: click a part to highlight it and see its name, group and
  bounding size; click empty space, the × in the part chip, or press Escape to
  clear it. **Gears** isolates the movement and complications; **Layers** shows
  the fully exploded stack. Deep links: `#gears`, `#layers`, `#dial`,
  `#crown`, `#three`.
- **Design-first controls.** Design Studio exposes the fields that change the
  look — case material and finish, dial finish and colour, crystal, hands and
  indices — as direct selects and swatches. Each control posts to `/api/set`,
  so the mesh, mass, checks and build package are rebuilt from structured data.
  Reference images can be attached and sent with a design request; the
  assistant is required to say when the configured model cannot see them.
  **Generate alternatives** calls the deterministic variant planner; nothing is
  applied until you choose a card.
- **Pareto Optimisation Lab.** The lab evaluates a deterministic field of real
  spec changes through the solver, mesh builder and verifier. Hard constraints
  are separate from five mixed-direction objectives (minimum mass/cost/
  manufacturability/assembly index, maximum tolerance margin). A selectable
  scatter splits the Pareto frontier from dominated and infeasible candidates;
  inspection shows objective deltas, constraints, verification and the baseline
  diff. A candidate can be loaded as a non-mutating 3-D preview, then applied
  only after explicit confirmation through the validated variant pipeline as a
  new version. Undo restores the prior solved design. The single-objective
  recommendation is a finite-probe suggestion, with the Pareto front reported
  separately and no global-optimality claim.
- **Movement database tab.** A small, source-gated catalogue of ETA 2824-2,
  Miyota 9015, Sellita SW200-1 and Seiko NH35A calibres lives in
  `watchcad/movements.py` and is fetched through the API, never embedded in the
  frontend. Each field is `verified`, `secondary`, `estimated` or `unknown`;
  blanks mean the source does not publish the value. Detail shows sources,
  per-field provenance, an envelope and structured PASS/WARNING/FAIL/UNKNOWN
  compatibility for case diameter/height, mounting, stem/crown, dial, hands,
  date alignment and caseback clearance. Nothing is applied without approval.

---

## How a design is produced

1. **Conversation → intent → patches.** A chat message is handled
   conversation-first. With a model configured, the model may answer directly or
   call validated tools (inspect, research, update a field, solve, verify,
   build, undo). Without a model, the deterministic parser still handles
   explicit CAD actions, and the reply says plainly that it is the local
   fallback. In both paths the rule parser remains available as a fallback for
   spec interpretation. No path writes geometry.
2. **Spec.** The document is a nested dict with a field registry, defaults,
   ranges, enums and provenance. Every write is tagged; user locks stop both
   prompts and the solver from overwriting a value.
3. **Solver.** Derivations run in dependency order: bore, dial, case, train,
   barrel, torque, stack z-levels, hand lengths, quartz current, mass and a
   checksum. Gear ratios are found with exact `Fraction` arithmetic; only the
   final presentation profiles are approximated.
4. **Parts.** Pure functions turn the solved spec into meshes with a small
   dependency-free kernel (revolve, extrude, tube, dome, torus, sweep, lens,
   chamfered disc, arc ring and knurl). Case/dial/hand furniture is spec-driven;
   PBR finishes and nominal densities live in one central material library.
5. **Verification.** Checks are labelled `calculated` / `estimated` /
   `needs_test`, and failures carry one-click fixes. A scene-level pass also
   reports missing or disconnected parts, layer order, envelope violations,
   collisions and invalid dimensions. Visual approximation, parametric geometry
   and validated solid geometry are reported as distinct things.
6. **Build.** Everything is written to one directory with a sha256 manifest.
   Rebuilding an unchanged spec is a cache hit.

The same pipeline serves the copilot, numeric controls, API and CLI — there is
no UI-only shortcut and no server-side "magic" beyond this code.

---

## The spec: provenance, locks, patches

A spec is plain JSON; `spec.json` in every package is the authoritative input.

```python
from watchcad.nlp import parse
from watchcad.solver import solve

result = parse("A 42 mm steel dive watch, date.")
spec = result.spec

spec.set("case.diameter_mm", 41.0, tag="user", note="typed in the form")
spec.lock("case.diameter_mm")                # solver and prompts now skip it
spec.get("derived.train.total_barrel_to_escape")
solved, report = solve(spec)
print(solved.summary())
```

Provenance tags:

| tag | meaning |
|---|---|
| `user` | typed or stated by the user |
| `derived` | computed by the solver, reproducible from other fields |
| `sourced` | copied from an external source; URL and timestamp are kept |
| `not_found` | looked for and not published; kept as `null` rather than invented |

Operations: `get`, `set`, `patch` (JSON-patch style), `diff`, `validate`,
`copy`, `to_json` / `from_json`, `from_dict`. `set()` increments
`meta.version` and refuses a locked path unless `force=True`.

Locks survive prompts: parsing "make it 44 mm" against a spec with
`case.diameter_mm` locked keeps the old value and reports `locked, kept:
case.diameter_mm` in the result.

Saved projects carry `schema_version`. `migrate_spec_dict()` upgrades older
documents to `SCHEMA_VERSION = 3` additively: existing values are preserved and
new fields come from current defaults, with a migration entry in the spec
history. Nothing that was previously supplied is guessed or overwritten.

---

## Verification without a single badge

There is deliberately no overall "PASS". Each check answers a different kind of
question:

- **`calculated`** — closed-form arithmetic or geometry predicates. The number
  or boolean is trustworthy inside the stated model.
- **`estimated`** — a model with documented assumptions (torque, mass, spring
  proportions). Trust the order of magnitude, not the last digit.
- **`needs_test`** — physics that only a bench can settle (rate, amplitude,
  water resistance, shock, magnetism, thermal behaviour).
- **fail** — an actual contradiction in the spec or geometry, with a `fix`
  object the UI can apply.

A typical 42 mm mechanical watch reports about 25 calculated, 6 estimated and
8 needs-test checks, 0 failures. A thin 40 mm × 5 mm case correctly fails
geometry, stack and tolerance checks instead of pretending to be buildable.

Layers: `spec`, `geometry`, `kinematics`, `energy`, `tolerance`,
`manufacturability`, `physical`.

---

## Higher-fidelity geometry and rendering

The deterministic builder in `watchcad/parts.py` adds profile transitions,
bezels, lugs, crown guards and grips, crystal curvature and edge detail,
caseback options, applied and printed indices, lume, detailed hands, date and
complication apertures, and dial textures from the same spec fields used by the
solver. It has explicit layer stacking and clearance fields rather than
decorative drawing-only shapes. `watchcad/mesh.py` supplies UV-mapped lens,
chamfer and arc-ring primitives; `watchcad/materials.py` is the one place for
PBR finishes and nominal densities.

These are **parametric mesh approximations**, not B-rep solids. Chamfers,
curved crystals and knurls are generated deterministically and labelled as
approximations; there are no booleans, fillet kernel, draft or screw threads.
`watchcad/verify.py` checks missing/disconnected parts, positions, collisions,
invalid dimensions, inconsistent references and generation failures in addition
to the classic engineering checks. A scene can render convincingly and still be
unmanufacturable — the verifier does not collapse that distinction into a badge.

Rendering runs through Low/Medium/High presets. The server caches solved scenes
per quality; GLB carries PBR information and UVs; the viewer reuses procedural
textures, caps pixel ratio and shadows, and renders on demand. Candidate
previews use short-lived session tokens and never replace the live design.

---

## Movement database and compatibility

`watchcad/movements.py` is the only server-side source of manufacturer data.
Four calibres ship with manufacturer URLs and a retrieval date; records are
versioned, validated on load, append-only corrected, and migrated independently
of the watch spec (`MOVEMENT_SCHEMA_VERSION = 1`). Every scalar has a
`verified`, `secondary`, `estimated` or `unknown` status. Missing values remain
`null` and appear as UNKNOWN in the UI; they are never silently treated as zero
or as a pass.

`GET /api/movements` provides searchable catalogue data with filters;
`GET /api/movements/detail?id=…` returns the record, sources, field provenance,
missing fields, an envelope and a structured compatibility report;
`POST /api/movements/propose` builds candidate spec changes without applying
them. Checks cover case diameter and height, mounting, stem/crown, dial,
hand clearance, date/complication alignment and caseback clearance. They use
stated assumptions such as case wall thickness and radial/stack clearances;
UNKNOWN never passes silently, and a proposal is applied only through the
normal approval pipeline.

---

## Pareto optimisation

`watchcad/optimise.py` evaluates a finite, deterministic probe set. Each
candidate is a real spec delta passed through the solver, mesh builder and
verifier. Schema validity, locks, solver success and verification failures are
hard constraints; objectives are mass, estimated unit cost,
manufacturability, tolerance margin and assembly simplicity, with mixed
minimisation/maximisation directions. Feasible candidates are compared with
Pareto dominance, NSGA-II non-dominated sorting and crowding distance. A
seed/signature records the baseline checksum, objective, axes, budget and
generator version so a repeated run is reproducible.

The lab shows a frontier/dominated/infeasible scatter with selectable X/Y
objectives, per-candidate constraints and baseline deltas, a non-mutating 3-D
preview, and explicit confirmation before applying as a new version. Undo
restores the previous solved design. The recommendation is the best feasible
candidate for the chosen single objective within this probe set; the Pareto
front is reported separately. No global or continuous optimum is claimed.

---

## Production package

`watchcad build` writes ~65 files (about 7 MB for a mechanical design):

```text
spec.json / spec.md          source of truth + readable summary
model.stl .obj .gltf .glb    assembly meshes (glTF 2.0 with UVs/normals/PBR)
parts/<part>.stl             one mesh per component (~114 parts by default)
drawings/front.svg           front view
drawings/section.svg         case section
drawings/train.svg           gear train layout
drawings/notes.svg           notes and title block
drawings/watch_sheet.dxf     ASCII DXF R12 sheet
drawings/watch_drawings.pdf  4-page drawing set
bom.csv / bom.md             bill of materials and rolled-up totals
assembly.md                  assembly sequence and notes
test_plan.md                 bench tests mapped to the checks
dfm.md                       design-for-manufacture notes
verification.md              full layered check report
checks.json                  machine-readable checks
step_status.json             honest STEP availability status
manifest.json                sha256 of every file
```

`step_status.json` reports STEP as **unavailable**: writing real STEP needs an
OpenCASCADE-class kernel, which is out of scope for the zero-dependency core.
No placeholder `.step` file is emitted.

---

## Conversational design assistant

The studio's primary interface is a conversation. A message is not automatically
turned into a spec edit: the runtime first decides whether to answer, ask a
clarifying question, inspect the current design, research a topic, or perform a
bounded CAD task. A message such as *"Why sapphire instead of mineral glass?"*
gets a prose answer and leaves the design untouched. *"Make the dial blue"*
becomes a validated tool call.

```bash
# One conversational turn (JSON)
curl -s -X POST http://127.0.0.1:8765/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"Why sapphire?","mode":"safe"}'

# The same turn streamed as server-sent events
curl -N -X POST http://127.0.0.1:8765/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"conversation_id":"conv_…","message":"Make the dial blue","mode":"safe","stream":true}'
```

With a model configured, the runtime uses native OpenAI-style tool calling and
supports multiple tool rounds, streamed text deltas, usage reporting, retries,
cancellation, conversation summarisation and multimodal image content. The
system prompt and tool catalogue enforce the same rule as the agent: the model
never emits geometry, coordinates, meshes, formulas or build scripts. Every
mutation is a typed tool call executed by `ToolExecutor` against a shadow copy,
then solved and verified before it can be committed. Locked fields and derived
fields are refused by the executor, not just by the prompt.

Without a model the runtime is explicit about the fallback. It can still carry
out unambiguous local CAD actions (change a field, repair, verify, build, undo)
through the deterministic engine, but it labels the result as **local rules**
and says that free-form conversation needs a configured model. A model failure
is reported as an error; it is never presented as an AI answer.

### Conversations, designs and memory

The transcript and the design document are stored separately but linked:

| table | contents |
|---|---|
| `conversations` | id, title, design-session id, summary, timestamps |
| `messages` | role, content, images, suggestions, engine, status, usage |
| `tool_events` | tool name, arguments, result, success, run id |
| `designs` | latest verified spec per design session |
| `design_refs` | which design versions a conversation touched |

The database defaults to `out/watchcad.sqlite3`; set `WATCHCAD_DB` to use a
different path. Opening a conversation restores its transcript and switches the
browser to its design session, so the legacy `/api/*` endpoints and the new chat
stay in sync. Long conversations keep a rolling summary while the full visible
transcript remains available. Starting a new chat clones the current design;
later changes to either chat no longer affect the other.

### Research and references

`research_topic` and `fetch_source` return curated reference entries with an
evidence status (`calculated`, `estimated` or needs bench testing) and a
provenance note. If `WATCHCAD_SEARCH_URL` is configured, live results also carry
their source URL and retrieval timestamp. Otherwise the reply says live search is
unavailable and uses the curated table only. A web snippet is never presented as
a manufacturer specification.

### Images

The composer accepts up to four image attachments. When a vision model is
configured, the bytes are sent as multimodal content with the turn. The
assistant may comment on style, proportions and visible features; it must not
infer exact dimensions from a photograph. Without a vision model the attachments
are still stored with the message, and the reply explains the limitation.

### Conversation endpoints

| method | path | body / query |
|---|---|---|
| POST | `/api/chat` | `{conversation_id?, message, images?, mode?, use_llm?, stream?, regenerate?, edit_message_id?, component?}` |
| POST | `/api/chat/stop` | `{conversation_id?, run_id?}` → cancel active runs |
| POST | `/api/chat/approve` | `{conversation_id, token}` → apply a staged chat change |
| POST | `/api/chat/reject` | `{conversation_id, token}` → discard a staged chat change |
| GET | `/api/conversations` | `?q=<search>` → conversation list |
| POST | `/api/conversations` | `{title?, design_session?}` → create a chat |
| GET | `/api/conversations/<id>` | transcript, tool events, design refs, pending approval, snapshot |
| POST | `/api/conversations/<id>/rename` | `{title}` |
| POST | `/api/conversations/<id>/delete` | delete the transcript and its messages |
| POST | `/api/conversations/<id>/messages/<mid>/delete` | delete one message |
| GET | `/api/settings` | non-secret model/research settings and configuration status |
| POST | `/api/settings` | update the non-secret settings file |
| GET | `/api/providers` | provider presets, active non-secret config and capability flags |
| POST | `/api/providers/save` | `{provider, endpoint, model, vision_model, key}` → persist non-secrets, hold the key in session memory |
| POST | `/api/providers/test` | `{provider, endpoint, model, key?}` → real probe with redacted errors |
| POST | `/api/providers/remove` | remove the current session key |

`POST /api/chat` with `"stream": true` emits `text/event-stream` events of type
`start`, `notice`, `status`, `delta`, `timeline`, `error`, `done` and a final
`result`, followed by `event: end`. Without streaming it returns the same result
as JSON. The legacy `/api/prompt` fields (`patches`, `questions`, `notes`,
`engine`, `applied`, `skipped_locked`) are unchanged.

---

## Autonomous design agent

The studio can run an autonomous, tool-using design loop on top of the same
deterministic engine. The model orchestrates; it never writes geometry, a mesh
or executable code. Each step is a validated tool call:

| kind | tools |
|---|---|
| read | `get_current_spec`, `get_verification_report`, `inspect_component`, `get_component_context`, `critique_design`, `get_field_dependencies`, `compare_specs`, `get_design_history`, `inspect_geometry` |
| act | `update_spec`, `apply_patch`, `run_solver`, `run_verification`, `build_package` |

Acting tools run on a shadow copy of the spec. The live design is replaced only
after the solver and verifier have run, and a failed attempt is rolled back.
Locked fields are refused by the tool executor itself, not merely by the prompt.
The loop is bounded (three repair iterations by default, configurable) and every
run reports the verification summary before and after the change.

### Component focus and the design critic

Selecting a part in the 3-D viewer (or its row in the HUD) sets a **component
focus**. The focus resolves scene part names, roles, viewer groups, aliases and
dotted fields through one registry (`watchcad/components.py`), for example
`crown`, `hour_hand`, `crystal_gasket`, `movement.complications.date` or the
`Crown` label. While a focus is active:

- chat and agent requests carry that selection, and mutating tools refuse any
  field the component does not own. The refusal names both components instead of
  silently editing the whole watch;
- the Critic tab shows the component's writable fields, values, locks, related
  verification checks and scene parts;
- clearing the selection returns edits to the whole design.

The **Critic** tab renders `watchcad/critic.py`: five deterministic axes —
proportions, dial legibility, aesthetic coherence, ergonomics and component
consistency. Every score and metric is computed from the solved spec, scene and
verification checks. Dial legibility and aesthetic coherence are explicitly
`estimated` heuristics, not AI taste claims; the other axes are `calculated`.
The report offers only schema-valid, unlocked safe fixes and never mutates the
design by itself. A language model may explain the report, but it does not
produce its numbers.

Known limits: the critic is a rubric, not a physical test or a production gate;
contrast is a WCAG calculation on modelled colours, not a lighting measurement;
mass is an envelope estimate; and `small_seconds` still renders as a central
second hand in the mesh builder.

### Autonomy modes

| mode | behaviour |
|---|---|
| `suggest` | Propose the validated change and its diff. Nothing is staged or written. |
| `approval` | Stage the change on a shadow copy, return an approval token, and write only after `POST /api/agent/approve`. |
| `safe` | Commit only when the change does not increase the number of failing checks; otherwise roll back. |
| `autonomous` | Commit after the solver and verifier succeed, even if unrelated checks still fail. A repair request (`"fix the failing checks"`) will attempt bounded verifier-guided repairs first. |

Precedence is fixed: user locks and constraints, then values stated explicitly
in the current request, then the model's interpretation, then the heuristic
parser, then defaults. The UI exposes the mode, a live tool/iteration timeline,
a spec diff, approval controls, stop, undo/redo, history and proposed variants.

```bash
# propose only
curl -s -X POST http://127.0.0.1:8765/api/agent/run \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"make the dial blue","mode":"suggest","use_llm":false}'

# autonomous, isolated browser session
curl -s -X POST http://127.0.0.1:8765/api/agent/run \
  -H 'Content-Type: application/json' \
  -H 'X-WatchCAD-Session: my-session' \
  -d '{"prompt":"fix the failing checks","mode":"autonomous"}'
```

Sessions are isolated by `X-WatchCAD-Session`, a `?session=` query parameter or
a `session_id` body field. The default session keeps using the process-wide
`STUDIO`, so existing scripts and `/api/prompt` are unchanged.

---

## HTTP API

`serve` starts a threaded `http.server` on `127.0.0.1:8765` by default. The UI
uses only these endpoints, so scripts can drive the same session:

| method | path | body / query |
|---|---|---|
| GET | `/api/state` | spec, checks, summary, scene stats, derivation report |
| GET | `/api/schema` | field registry, groups, complications, presets, ideas |
| GET | `/api/scene` | `?quality=low|medium|high&preview=<token>` → per-part vertices/faces and scene stats |
| GET | `/api/component/context` | `?component=<id, alias, part, role, group or field>` → fields, locks, checks, parts and bounds |
| GET | `/api/critic` | `?component=<optional selection>` → deterministic five-axis report and safe fixes |
| GET | `/api/model.glb` | `?quality=low|medium|high&preview=<token>` → binary glTF for the Three.js preview |
| GET | `/api/movements` | `?q=&maker=&type=&min_diameter=&max_diameter=&min_thickness=&max_thickness=&include_unknown=1` → catalogue |
| GET | `/api/movements/detail` | `?id=<calibre>` → record, sources, provenance, envelope and compatibility |
| POST | `/api/movements/propose` | `{id}` → unapplied spec-change proposal |
| GET | `/api/builds` | last build manifest |
| POST | `/api/prompt` | `{prompt, use_llm}` → applied patches, questions, state |
| POST | `/api/set` | `{path, value, force}`; `409` if the path is locked |
| POST | `/api/patch` | `{ops:[…]}` |
| POST | `/api/lock` | `{path, locked}` |
| POST | `/api/spec` | `{spec}` replace the whole document |
| POST | `/api/reset` | `{preset?, name?}` |
| POST | `/api/build` | `{per_part?}` → files and manifest |
| POST | `/api/optimise` | `{objective, budget, axes?}` → Pareto fronts, winner, constraints and reproducibility |
| POST | `/api/optimise/preview` | `{changes, label, baseline_checksum?}` → short-lived preview token; `409` if the baseline changed |
| GET | `/api/download/file` | `?path=<relative path inside the build>` |
| GET | `/api/download/zip` | package as one archive |

Example:

```bash
curl -s http://127.0.0.1:8765/api/state | python3 -m json.tool | head
curl -s -X POST http://127.0.0.1:8765/api/prompt \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"36 mm titanium quartz watch, white dial","use_llm":false}'
curl -s -X POST http://127.0.0.1:8765/api/build \
  -H 'Content-Type: application/json' -d '{"per_part":true}'
curl -s -o package.zip http://127.0.0.1:8765/api/download/zip
```

Downloads are restricted to the build directory; traversal attempts return
`403`. The server binds to localhost, has no authentication and is not intended
for exposure to a network.

Agent and session endpoints:

| method | path | body / query |
|---|---|---|
| POST | `/api/agent/run` | `{prompt, mode?, use_llm?, max_iterations?, stream?, component?}`; streaming returns SSE frames |
| POST | `/api/agent/approve` | `{token}` → apply a staged change |
| POST | `/api/agent/reject` | `{token?}` → discard a staged change |
| POST | `/api/agent/cancel` | `{run_id?}` → stop a running loop |
| POST | `/api/agent/undo` | restore the previous version |
| POST | `/api/agent/redo` | restore the next version |
| POST | `/api/agent/variant` | `{variant:{changes:[…]}}` → validate and apply one proposal |
| GET | `/api/agent/history` | versions, conversation, design memory, pending approval |
| GET | `/api/agent/version` | `?id=<version-id-or-index>` → full historical spec |
| GET | `/api/agent/tools` | tool catalogue plus knowledge summary |
| GET | `/api/agent/nlp` | `?q=<text>` → deterministic intent |
| GET | `/api/agent/knowledge` | `?q=<text>&category=…` → materials/components |
| GET | `/api/agent/variants` | deterministic variation proposals |
| GET | `/api/session` | current session, conversation and memory |
| GET | `/api/sessions` | list isolated sessions |
| POST | `/api/session/new` | `{title?}` → create a session |
| POST | `/api/session/drop` | drop the current non-default session |

`POST /api/agent/run` with `"stream": true` emits `text/event-stream` frames of
type `start`, `timeline`, `result` or `error`, followed by an `end` event.
Without streaming it returns the same result object as JSON. The existing
`/api/prompt` fields (`patches`, `questions`, `notes`, `engine`, `applied`,
`skipped_locked`) are preserved.

---

## Optional LLM

Rules always run and always produce a complete result. To let an
OpenAI-compatible model answer the conversation and call validated tools:

```bash
export WATCHCAD_LLM_URL=http://127.0.0.1:11434/v1/chat/completions
export WATCHCAD_LLM_KEY=...              # optional
export WATCHCAD_LLM_MODEL=...            # optional
export WATCHCAD_LLM_PROVIDER=openai      # optional provider hint
export WATCHCAD_LLM_VISION_MODEL=...     # optional, enables image turns
export WATCHCAD_LLM_TIMEOUT=60           # optional, seconds
export WATCHCAD_LLM_TEMPERATURE=0.3      # optional
export WATCHCAD_LLM_MAX_TOKENS=0         # optional, 0 = provider default
export WATCHCAD_LLM_RETRIES=1            # optional transient retries
export WATCHCAD_LLM_RETRY_DELAY=0.4      # optional, seconds

# Optional live research; without it the curated table is used and labelled
export WATCHCAD_SEARCH_URL=...
export WATCHCAD_SEARCH_KEY=...           # optional
export WATCHCAD_SEARCH_PROVIDER=...      # optional

export WATCHCAD_DB=out/watchcad.sqlite3  # optional conversation database
export WATCHCAD_SETTINGS=out/settings.json
python3 -m watchcad serve --open
```

Setting `WATCHCAD_LLM_URL` (or saving an endpoint in **Settings**) enables the
model-first chat and the optional prompt pass. Pass `--no-llm` (CLI), uncheck
**Use model**, or send `"use_llm": false` (API) to force the deterministic path.
Every model reply is validated; invalid, out-of-range or locked values are
dropped, and the run says so. With no variables set, no network request is
attempted and the local CAD engine remains fully usable.

Secrets (`WATCHCAD_LLM_KEY`, `WATCHCAD_SEARCH_KEY`) are read only from the
environment, unless a session key is supplied through the AI provider dialog
and held in process memory. The settings JSON and `/api/settings` expose
provider, endpoint, model, vision model, temperature, token limit, timeout and
retry policy, but never a key.

### Bring your own key, safely

The **AI provider** dialog is the session-only alternative to environment
variables:

| provider | endpoint style | notes |
|---|---|---|
| Google Gemini | OpenAI-compatible Gemini endpoint | streaming, tools, JSON, vision |
| Cloudflare Workers AI | account-scoped OpenAI-compatible endpoint | replace `<ACCOUNT_ID>` |
| Groq | OpenAI-compatible inference | streaming, tools, JSON |
| OpenRouter | one key for many model vendors | vision depends on model id |
| OpenAI-compatible | any `/v1/chat/completions` endpoint | local servers welcome |

**Test connection** issues small real requests for chat, streaming, tools and
JSON mode; **Save for session** persists only the non-secret provider, endpoint,
model and optional vision model, and keeps the key in the server process'
in-memory `ProviderVault` keyed by `X-WatchCAD-Session`. **Remove key** clears
that session entry. Keys never enter the settings file, `localStorage`, the
static bundle, logs or any JSON response; error text is redacted. Custom
endpoints must use `http://` or `https://`, which still permits a local model
server. Restarting the server forgets all keys. An injected `LLM_CONFIG` (used
by tests and embedders) still takes precedence over session keys.

Capabilities are reported with their source. Presets state what the provider is
expected to support; a successful probe replaces those flags with measured
results. Image support is inferred from the model name or an explicit vision
model — it is never claimed just because a provider has a vision-capable cousin.

---

## Architecture

```text
watchcad/
  schema.py      field registry, Spec, provenance, locks, patches, validation
  modules.py     complication registry: height, torque, envelope, conflicts
  gears.py       exact Fraction ratio search; presentation tooth profiles
  solver.py      derivation order, train layout, stack z-levels, torque, mass
  mesh.py        dependency-free mesh kernel, UVs + STL/OBJ/glTF/GLB writers
  materials.py   central PBR finishes and nominal densities
  parts.py       every component builder; build_scene(); quality tiers
  verify.py      layered checks, same-plane collision, scene validation, fixes
  components.py  component registry, selection resolver, field/check/part scope
  critic.py      deterministic five-axis design critic and safe fixes
  movements.py   source-backed calibres, provenance, migration, compatibility
  optimise.py    deterministic candidates, hard constraints, Pareto fronts
  providers.py   BYOK presets, session key vault, capability probes
  drawing.py     SVG, ASCII DXF and hand-written PDF from one canvas
  exporters.py   BOM, assembly, test plan, DFM, docs, manifest
  build.py       orchestration, determinism and cache
  nlp.py         rule parser, presets, essential questions, optional LLM
  llm.py         provider-aware OpenAI-compatible client: tools, streaming, retries, vision
  store.py       SQLite conversations, messages, tool events, designs, summaries
  settings.py    non-secret JSON settings with environment precedence
  api.py         ThreadingHTTPServer studio, chat, conversations, sessions, downloads
  cli.py         new / prompt / verify / build / export / presets / show / serve
  agent/
    chat.py           conversation-first runtime, model/tool loop, local fallback
    orchestrator.py   run loop, planning, bounded repair, commit/stage/rollback
    planner.py        deterministic intent detection, local plan, variants
    tools.py          typed tool schemas and read/action/research/history handlers
    tool_executor.py  argument validation, shadow copy, diff, commit, cancel
    evaluator.py      verification outcome and repair candidates
    context.py        sessions, conversation, design memory, versions, approvals
    models.py         JSON-serialisable agent state and timeline dataclasses
    prompts.py        conversation/agent system prompts and model context
    research.py       curated + optional live research, provenance, SSRF guard
    knowledge.py      materials, components and finishes reference
web/
  index.html, style.css, app.js   workspace UI, design/movement/Pareto controls, chat
  viewer3d.js                     Three.js PBR/IBL preview, quality and candidate previews
  vendor/                         vendored Three.js 0.160 modules and addons
tests/
  test_watchcad.py               engine, glTF/GLB, build, export tests
  test_agent.py                  deterministic agent/API tests
  test_chat.py                   conversational runtime/API tests
  test_optimise.py               Pareto dominance, constraints, reproducibility, API
  test_providers.py              key isolation, redaction, probe and provider API
  test_increment_a.py            component focus, scoped edits, critic determinism/API
  test_geometry.py               PBR/materials, quality invariance, mesh/scene validation
  test_movements.py              provenance, migration, corrections, compatibility
  ui_smoke.js                    dependency-free browser/API smoke harness
examples/                         seven verified specs
```

Some deliberate design choices:

- **Exact ratios.** Wheel/pinion combinations are enumerated as `Fraction`s, so
  target ratios are achieved exactly (error is reported in ppm) instead of
  being approximated by floating-point searching.
- **Stack from the crystal down.** Z-levels are anchored at the top, so a
  design stays visually coherent when the case gets thicker or thinner.
- **Planes, not just radii.** Wheels on different heights may overlap in plan;
  same-plane collisions are checked with real clearances.
- **Barrel bore = 34 % of movement width.** A documented empirical rule, kept in
  one place and surfaced as an `estimated` check rather than a hidden constant.
- **One canonical spec.** Quality tiers change rendering cost, not dimensions or
  verification; movement manufacturer data stays on the server and is served
  through the API.
- **No fake STEP, no fake physics.** The package says what it cannot do.

---

## Honest limitations

- **STEP is unavailable** without an OpenCASCADE-style kernel. STL/OBJ/glTF are
  real meshes; the DXF is 2D geometry for drawings, not a solid model.
- **Gear tooth profiles are presentation-grade** radial-cycloidal
  approximations. Ratios are exact; contact stress, backlash and true involute
  conjugate action are not modelled.
- **Torque, spring and mass models are empirical** with documented constants.
  They are `estimated`, not validated against a specific calibre.
- **Dial/aperture furniture is layered geometry, not boolean cuts.** Date,
  moonphase and sub-dial detail is built as frames, discs and rings above the
  dial rather than by cutting a solid; no B-rep boolean operation is performed.
- **Movement coverage is deliberately small.** Four manufacturer-sourced
  calibres are included; a blank field stays UNKNOWN rather than being guessed.
  Live search is opt-in through `WATCHCAD_SEARCH_URL`, and a retrieved page is
  still text, not a validated datasheet. There is no general RAG corpus.
- **No solid fillets, drafts, screw threads or B-rep booleans.** Deterministic
  mesh chamfers, lens edges and knurls are generated, but they are visual
  approximations, not manufacturing features. Jewels as bearings, lubrication,
  escapement dynamics, FEA, tolerance Monte-Carlo and CAM toolpaths are absent.
- **Assembly-wide interference is not proven.** Tooth outlines and scene-level
  envelopes/clearances are checked, but there is no general part-vs-part mesh
  intersection or swept-volume study.
- **Bench properties are `needs_test`.** Rate, amplitude, water resistance,
  shock, magnetism and thermal behaviour cannot be certified by this tool.
- **The web UI is a Three.js GPU viewer with a software fallback.** It offers
  PBR/IBL, shadows, orbit/pan/zoom, quality tiers, view presets, exploded views,
  per-part picking and non-mutating candidate previews. The classic canvas
  fallback is depth-sorted triangles. Neither has section planes, measurement
  tools or distance-based LOD; quality tiers are performance hints only.
- **Conversations persist, but there are no user accounts.** Chat transcripts,
  design snapshots, tool events, summaries and design references live in the
  SQLite file (`out/watchcad.sqlite3` by default) and survive a restart. Session
  headers separate browser tabs, not untrusted users. There is no
  authentication layer, so the server remains a local studio.
- **Hosted/multi-user deployment is out of scope.** The server binds to
  localhost, has no authentication and is not intended for exposure to a
  network.
- **The agent is bounded, not open-ended.** It can hold a conversation, interpret
  a request, use validated tools, repair within three iterations by default,
  critique, explain, propose variants and build a package. Pareto analysis is a
  separate deterministic tab, not an autonomous agent capability. Optimisation
  is synchronous with no durable job queue, progress stream or resumable run.
  There are no learned layout proposals, Electron shell, module marketplace or
  live collaboration.
- **Vision input is advisory.** A configured vision model can discuss a photo's
  style, proportions and visible features, but the assistant must not infer
  exact dimensions from it. Without a vision model the image is stored with the
  message and the limitation is stated.

---

## Tests and verification coverage

The suite covers the parts that must not silently break:

- schema defaults, round-trip, provenance, versions, locks, diff/patch,
  validation and additive `v1 → v3` migration;
- exact gear ratios and bounded tooth outlines;
- mesh volume/winding, UVs/PBR extras and all three mesh exporters;
- material/finish centralisation, lens/chamfer/arc primitives, spec-driven
  case/dial/hand detail and quality-tier invariance of dimensions/verification;
- scene validation for missing/orphaned parts, layer order and collisions;
- mechanical and quartz solving, stack ordering, centre distances, checksum;
- layered verdicts, failure fixes, complication conflicts;
- four drawing views, SVG/DXF/PDF structure;
- package contents, manifest hashes, STEP status, build cache;
- prompt parsing, preset coverage, locks and complication conflicts;
- movement provenance, unknown fields, migration/validation, corrections,
  catalog search, envelope and structured compatibility;
- Pareto dominance, non-dominated sorting/crowding, mixed objectives, hard
  constraints, reproducibility, preview/apply/rollback and the optimisation API;
- the full HTTP session: state, schema, movement and optimisation endpoints,
  scene/GLB quality and preview, set, lock `409`, prompt, build, file/zip
  download, traversal `403`;
- the agent: shadow-copy commits, suggest/approval/autonomous modes, locked-field
  denial, bounded verifier-guided repair, undo/redo, variants, tool-argument
  validation, cancellation, scripted model tool calls, LLM fallback, prompt
  precedence, intent detection and the streaming agent endpoints;
- the conversation runtime: direct Q&A without CAD mutation, follow-up context,
  opinion/discussion turns, validated tool changes, approval staging, locked
  and derived-field rejection, research provenance, undo, long-conversation
  summarisation, reload, image handling, honest local fallback, model failure,
  message edit/regenerate/delete, SSE framing, conversation CRUD/search and
  settings secrecy;
- CLI `new`, `verify` and `export`.

The UI was exercised headlessly with Node against the real API (boot, chat SSE
stream, attachments, history sidebar, settings, message edit/regenerate/delete,
chat approval, copilot brief, undo, number steppers, explode 0–250%, deep zoom,
per-part picking, component focus chip, the five-axis critic panel, critic fixes,
movement filters/detail/proposal, Pareto scatter axes, frontier/dominated split,
candidate inspection, confirmation, non-mutating 3-D preview and rollback,
opaque materials, gear/layer views, zoom, projection sanity) and in real Firefox
through Marionette (rendered screenshots, stepper click, copilot brief, hover +
part selection, sidebar close/reopen with persistence, zero runtime errors).

---

## License

MIT — see [`LICENSE`](LICENSE). The original project brief remains the property
of its author.
