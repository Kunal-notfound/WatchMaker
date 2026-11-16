# WatchMaker 2.0 — feature-gap report

**Snapshot:** `05326fe` (local `main`, one commit ahead of `origin/main`; nothing
pushed), plus **Increment A** in the working tree (uncommitted). 119 Python tests
and the Node UI smoke suite pass on this tree.

**Implementation log:** Increment A (component-aware editing + deterministic
five-axis critic) is implemented in the working tree. The scorecard rows for
"AI design critic" and "Conversational component editing" are updated to ✅;
the original gap analysis below is kept for context.

**Method:** read the schema, solver, mesh/parts, verifier, optimiser, exporters,
store, agent tools/planner/chat/knowledge/research, HTTP API, and the web client
(`app.js`, `viewer3d.js`, `index.html`). Capabilities are only marked **exists**
when they are implemented end-to-end and covered by a test or documented UI
behaviour. Nothing was inferred from the product brief alone.

**Status legend**

| Mark | Meaning |
| --- | --- |
| ✅ | Implemented and exercised by the suite or documented UI behaviour |
| ◐ | Partly implemented; an important part of the requested feature is missing |
| ✗ | Not present in this repository |

---

## Summary scorecard

| Requested area | Status | Short verdict |
| --- | --- | --- |
| Image-to-design | ◐ | Images can be uploaded and discussed by a vision model; no structured extraction into the spec |
| AI design critic | ✅ | Deterministic five-axis rubric with scores, evidence, findings and safe fixes (`critic.py`, `/api/critic`, Critic tab); no LLM-generated numbers |
| Design variations with explanations | ✅ | Existing variant pipeline, deterministic rationales, preview-before-apply |
| Conversational component editing | ✅ | Viewer selection resolves to a component scope; mutating tools and the planner refuse out-of-scope fields (`components.py`, focus threading) |
| Component isolation / visibility / selection | ✅ | Group visibility, isolate presets, per-part picking, hover and selection HUD |
| Section-plane inspection | ✗ | Only 2D section drawings, no live clipping plane |
| Distance / angle / clearance measurement | ✗ | No on-model measurement tools |
| Exploded views / highlighting | ✅ | Explode 0–250 %, per-part tint, gear and layer presets |
| Version history / snapshots / undo / redo | ◐ | Server-side history and undo/redo exist; no side-by-side visual comparison UI |
| Render/engineering consistency | ◐ | One scene feeds glTF and the software renderer; no automated pixel/geometry equivalence test |
| Knowledge base: movements/calibres | ◐ | Three generic movement envelopes, no calibre database |
| Knowledge base: materials/processes/components | ◐ | 20 curated entries; thin coverage, most lack source URLs |
| RAG / source-grounded retrieval | ◐ | Curated lookup plus optional live search with URL/timestamp; no chunked corpus or embeddings |
| Multi-objective optimisation | ✅ | Five deterministic objectives, real solver+mesh+verifier per candidate |
| Pareto frontier | ✗ | Candidates are ranked by one scalar score; no non-dominated set |
| Tolerance stack / DFM / interference | ◐ | Worst-case stack, DFM and tooth-outline interference exist; no assembly-wide interference or supplier constraints |
| Structural constraints | ✗ | No FEA, stress, deflection or fatigue model |
| Movement compatibility | ◐ | Width/height fit and beat-rate checks; no calibre-specific mounting or hand-height database |
| True STEP export | ✗ | Status explicitly reports STEP unavailable without an OCC-style kernel |
| Solid booleans / fillets / chamfers / threads | ✗ | Mesh builders only; dial openings are surface features |
| True involute gears | ✗ | Exact ratios, presentation-grade cycloidal profiles |
| BOM / manufacturing docs | ✅ | BOM, process notes, test plan, drawings, manifest hashes |
| Versioned spec / migration | ◐ | `meta.version` and exported `schema_version: 1`; no migration chain |
| Background jobs | ◐ | Threaded HTTP server, cooperative cancel/timeout; optimisation runs synchronously in-request |
| Provider usage / cost | ◐ | Token usage is reported when the provider returns it; no price table, aggregation or persistence |
| Mesh LOD / simplification | ✗ | Fixed tessellation, no distance-based LOD or decimation |
| Session / project isolation | ✅ | Per-session studio, conversation-scoped design sessions, session-scoped BYOK keys |
| Cross-layer regression tests | ◐ | Strong server/spec/geometry tests and UI smoke; no automated spec↔render↔CAD equivalence assertions |

---

## 1. AI design intelligence

### 1.1 What already exists

- **Image upload and vision chat** — up to four images per turn, data-URL
  validation, size limits, re-sent only for recent turns, and a vision-model
  setting per provider (`web/app.js`, `watchcad/agent/chat.py`,
  `watchcad/llm.py`). The UI also has an explicit "analyse this reference"
  action.
- **Honest vision policy** — prompts forbid inferring exact dimensions from a
  photo and require visual impressions to be labelled as impressions
  (`watchcad/agent/prompts.py`).
- **Variations** — deterministic blueprints with labels, rationales, changed
  fields, per-candidate verification, preview before apply, and the existing
  validated variant pipeline (`watchcad/agent/planner.py`,
  `/api/agent/variants`, `/api/agent/variant`).
- **Searchable curated knowledge** — `/api/agent/knowledge` and the
  `lookup_reference` tool; the agent can cite curated entries and research
  results with provenance.
- **Component inspection** — `inspect_component` resolves a name to parts,
  bounds, volume, triangle count and train nodes; `inspect_geometry` returns the
  scene tree and mass; `get_field_dependencies` explains knock-on effects.
- **Selected-part context** — clicking a mesh selects it, shows a HUD with name,
  group and bounding-box size, and emits `watchcad:select-part`.

### 1.2 What is missing

- **No image → structured specification workflow.** There is no extraction
  schema, no calibrated proportion estimate, no confidence/provenance fields,
  and no "accept these extracted fields" review step. Vision output is prose in
  the conversation, not spec patches.
- **No sketch/photo decomposition.** Case shape, bezel, lugs, dial layout,
  indices, hands, colours and finish are not turned into candidate field values
  with uncertainty.
- **No structured design critic.** `planner.critique()` produces a prose
  strengths/risks/direction summary from checks and derived metrics. There are
  no explicit axes, no per-axis score, weight, evidence or issue list for
  proportions, dial legibility, aesthetics, ergonomics and component
  consistency.
- **Component editing is name/path based, not selection-based.** The selected
  part is not injected into the agent context as the edit target, and there is
  no per-component override model (for example a dial-specific colour that
  differs from the global field).
- **No image-derived design lineage.** Uploaded references are not stored as
  design sources with retrieval dates and confidence.

### 1.3 Genuinely new

- A deterministic **image-brief extractor**: vision model returns a bounded JSON
  observation (no invented dimensions), the server validates it against the
  field registry, converts only defensible observations into patches, attaches
  confidence and `source: image`, and asks the user to confirm essentials.
- A **structured critic** with named axes and deterministic evidence collectors,
  returning scores, issues, fixes and provenance. The LLM may explain the
  report; it must not invent the numbers.
- **Selection-scoped editing**: selected component → allowed fields, current
  values, relevant checks, and a validated conversational edit path.

---

## 2. Advanced 3D workspace

### 2.1 What already exists

- Three.js viewer with IBL, shadows, orbit/pan/zoom-to-cursor, fit and named
  views (`three`, `dial`, `crown`, `gears`, `layers`).
- Group visibility toggles, isolate presets (`Movement`, `Complications`), dimmed
  excluded groups, per-part hover/selection tint, and a selection HUD.
- Exploded assembly from 0–250 %, layer clustering and rigid-band offsets.
- Component highlighting on hover and selection; picking ignores hidden parts.
- Server-side history: checkpoints, labelled versions, undo/redo, version
  lookup, `compare_specs`, design references, persisted design snapshots.
- The same scene payload drives the glTF export and the no-WebGL canvas
  fallback, so parts, colours, bounds and mass come from one source.

### 2.2 What is missing

- **Live section plane.** No clipping plane, no cap geometry, no axis/offset
  control, and no way to inspect the movement internals in 3D. The existing
  "section" is a 2D drawing view.
- **Measurement tools.** No pick-pick distance, angle, radius, wall-thickness or
  clearance measurement; no snapping to vertices/edges/faces; no measurement
  list or unit readout.
- **Side-by-side visual comparison.** History is textual/spec-level. There is no
  split viewport, ghost overlay, difference highlighting or before/after camera
  sync.
- **Per-part visibility.** Visibility is group-level; the API accepts a hidden
  group set. A single part cannot be hidden independently.
- **Reliable undo/redo across UI and agent.** Undo/redo exists in the server
  session, but there is no unified client-side gesture history for camera,
  visibility, explode, selection and spec edits.
- **Automated render/geometry consistency test.** The shared payload is a good
  design, but no test asserts that exported glTF bounds/parts match the solved
  scene.

### 2.3 Genuinely new

- Section plane (X/Y/Z, offset, flip) implemented in Three.js with an optional
  cap plane, plus a software-renderer fallback (clip triangles by plane).
- Measurement mode: point snapping, distance/angle/clearance, screen-space
  labels, and a measurement list that can be cleared.
- Snapshot comparison: two versions in one viewport (side-by-side or overlay),
  driven by stored spec versions.
- Per-part visibility and isolation layered on top of existing group state.

---

## 3. Horology knowledge base

### 3.1 What already exists

- `watchcad/agent/knowledge.py`: **20 curated entries** — 5 materials, 3
  crystals, 3 movement envelopes, 3 standards, 3 processes, 3 components.
- Every entry has a status (`reference`, `standard`, `estimate`) and a label;
  lookups are tiered (exact name/alias, prefix/substring, then summary text).
- `research.py`: optional live search and source fetch with SSRF protection,
  size caps, URL and retrieval timestamp, and a clear "unavailable" result.
- Spec provenance supports `sourced`, `source_url`, timestamps and `not_found`.
- Prompt guidance forbids inventing specifications and requires labelling
  estimates.

### 3.2 What is missing

- **No movement/calibre database.** Only three generic envelopes. There is no
  table of real calibres with dimensions, mounting, stem height, hand sizes,
  jewels, beat rate or source. This is the largest knowledge gap and the easiest
  place to accidentally invent data, so it must be source-gated.
- **Thin material properties.** Density and hardness only; no elastic modulus,
  yield strength, thermal expansion, machinability or supplier form data.
- **Thin process data.** No feeds/speeds, tolerances by process, minimum feature
  size, surface finish capability, or cost drivers.
- **No standard fasteners/components catalogue.** No screw standards, spring
  bars, gaskets, O-ring sizes, crystals by diameter/thickness, crowns/tubes,
  caseback threads or sealing systems.
- **No assembly/engineering constraint model** beyond the verifier's stack and
  fit checks.
- **No RAG.** Lookup is keyword matching over a hand-written list. There is no
  document corpus, chunking, embedding/index, hybrid retrieval, reranking or
  citation-anchored answer construction.
- **Provenance is structural but not populated.** Most curated entries have no
  `source_url` or `retrieved_at`; the default note honestly says no datasheet
  was fetched.

### 3.3 Genuinely new

- A source-gated calibre dataset with explicit `verified | manufacturer |
  community | estimate` status, URL, retrieval date, and "unknown" fields that
  are never filled by guesswork.
- Expanded material/process/component tables with units and test methods.
- A local, dependency-free retrieval layer (token index + BM25-style scoring)
  over curated documents and user-imported datasheets, returning passages with
  source spans. No third-party packages, no network in tests.
- A knowledge answer builder that refuses to answer when no source supports the
  claim.

---

## 4. Advanced engineering optimisation

### 4.1 What already exists

- `watchcad/optimise.py`: five objectives — weight, cost, manufacturability,
  tolerance, assembly. Each candidate is a real set of spec changes; the solver,
  mesh builder and verifier run per candidate. Nothing is applied automatically.
- Metrics include mass, material/process cost, part and complication counts, DFM
  failures, tolerance failures, stack margin and failing checks, each with a
  provenance label (`calculated` / `estimated` / `needs_test`).
- Candidate ranking puts passing candidates first, then score; baseline and
  changed metrics are reported.
- Verifier already covers: movement/dial fit, stack and hand clearance, gear
  centre distances and outline interference, tooth conflicts, kinematics, power
  budget/reserve, worst-case tolerance stack, DFM (water resistance, lug width,
  hand/crystal clearance) and physical `needs_test` items.
- Variants are applied through the validated pipeline, with locks respected.

### 4.2 What is missing

- **Pareto frontier.** Ranking collapses multiple objectives into one scalar.
  There is no non-dominated set, no trade-off table, no knee-point selection and
  no multi-objective search (for example NSGA-II style) — only single-objective
  blueprint ordering.
- **Structural constraints.** No stress, deflection, fatigue, impact or FEA.
  "Structural" is currently limited to geometric fit and interference.
- **Assembly-wide interference.** Tooth outlines are checked, but no general
  part-vs-part mesh intersection or swept-volume check for hands, rotor,
  caseback, crown/stem and complications.
- **Supplier/process constraints.** Cost tables are global constants; no supplier
  capability, minimum order, tolerance capability, lead time or process routing.
- **Tolerance stack sophistication.** Worst-case arithmetic only; no RSS/statistical
  stack, no sensitivity ranking, no Monte-Carlo, no measured distributions.
- **Movement compatibility beyond envelope.** No calibre-specific mounting,
  stem/crown alignment, hand tube diameters, date-window position or rotor
  clearance.
- **Batch/background optimisation.** The endpoint runs synchronously and holds
  the studio lock; there is no job queue, progress stream or resumable run.

### 4.3 Genuinely new

- Pareto analysis over the existing candidate metrics: non-dominated sorting,
  frontier table, per-objective deltas and explicit dominated-by links.
- Assembly interference using deterministic mesh/AABB + triangle tests, reported
  as `calculated` with tolerance assumptions stated.
- A constraint model with hard/soft constraints, supplier capability records and
  pass/fail evidence. Unknown supplier data stays unknown.
- Optional Monte-Carlo tolerance study with a fixed seed and documented
  distributions (clearly `simulated`, never `physically verified`).

---

## 5. Manufacturing and CAD

### 5.1 What already exists

- Mesh kernel: revolve, tube, frustum, dome, torus, extrude, rounded slab, sweep,
  merge, outward winding and signed volume.
- Real STL (binary/ASCII), OBJ and glTF/GLB export; DXF/SVG/PDF drawing set;
  BOM, process notes, test plan, manifest hashes and build cache.
- Exact gear ratios and bounded meshing outlines; gear geometry feeds the scene
  and interference checks.
- Package reports STEP explicitly as unavailable, with the reason and the
  optional-OCC path documented.
- Result provenance separates calculated, estimated, simulated and
  needs-test; the optimiser reports `simulated: false` and
  `physically_verified: false`.

### 5.2 What is missing

- **Solid kernel.** No OpenCASCADE (or equivalent) integration, no B-rep,
  no true STEP/IGES. The "optional OCC backend" is a documented intention, not
  code in this repository.
- **Booleans.** No union/subtract/intersect. Date windows, subdials and
  moonphase apertures are surface features, not cuts.
- **Fillets, chamfers, drafts, threads.** No edge blending or thread forms;
  screw-down crown/caseback are represented by simplified geometry.
- **True involute gears.** Ratios are exact, but profiles are presentation-grade
  cycloidal approximations; no conjugate action, backlash, contact ratio or
  stress.
- **Manufacturing documentation depth.** No operation sheets with setup,
  fixtures, tooling, inspection gauges, tolerance capability or CAM toolpaths.
- **Validation evidence.** No material certificates, no first-article
  inspection, no bench data ingestion.

### 5.3 Genuinely new

- A kernel adapter interface with a dependency-free mesh backend and an optional
  OCC backend. STEP export only when a real kernel is installed; otherwise the
  existing honest "unavailable" status remains.
- Mesh boolean/fillet/chamfer improvements where they can be done deterministically
  without a B-rep, each labelled as an approximation.
- A gear module that can switch between the existing presentation profile and a
  true involute profile, with a test that verifies conjugate contact at the pitch
  point and documents remaining limits.

---

## 6. Project reliability

### 6.1 What already exists

- `meta.version` increments on writes; exports carry `schema_version: 1`.
- SQLite store: conversations, messages, tool events, summaries, latest design
  per session, conversation→design references.
- Per-session studio and conversation-scoped design sessions; new conversations
  clone then diverge; session-scoped BYOK keys live only in process memory.
- Agent run control: cooperative cancellation, wall-clock budget, bounded
  repair iterations, streaming timeline events.
- Token usage is captured from provider responses and surfaced in run results.
- Spec, solver, geometry, exports, API and agent paths have 98 Python tests;
  the UI has a headless smoke suite.

### 6.2 What is missing

- **Spec migration.** `schema_version` is written but never read; there is no
  `migrate(old) -> new` chain, no compatibility tests across versions.
- **Decision log.** Design references record checksums and labels, but not the
  rationale, alternatives considered, or who/what decided. Agent memory is
  conversation-scoped.
- **True background execution.** Long optimisation/build work is synchronous
  in the HTTP request; cancellation exists but there is no durable job record,
  progress, retry or resume.
- **Provider cost tracking.** No per-provider price table, per-run cost,
  monthly aggregation, budget guard, or persisted usage ledger.
- **LOD.** No mesh decimation or distance-based switching; large assemblies rely
  on modest tessellation.
- **Cross-layer regression tests.** No test asserts that a spec change produces
  matching agent description, scene bounds, glTF part names and drawing labels.
- **User isolation/authentication.** Session isolation is not security; the
  server is localhost-only and unauthenticated by design.

### 6.3 Genuinely new

- Versioned spec migrations with fixtures for every historical version.
- A job runner (in-process, dependency-free) with status, progress, cancel and
  result retrieval, used by optimisation and package builds.
- A provider usage ledger: tokens, model, endpoint, estimated cost, timestamp;
  keys never persisted. Cost tables are explicit and editable.
- Mesh LOD tiers generated deterministically from the same geometry, with a UI
  quality control and triangle-count readout.
- A "consistency contract" test: one solved spec → agent summary fields, scene
  parts/bounds, glTF node names and drawing labels must agree.

---

## 7. Genuinely new work, ranked by the requested priority

| Rank | Feature | Why it is new | Dependency |
| --- | --- | --- | --- |
| 1 | Selection-scoped component editing | Selected part is not an edit target; no per-component overrides | Existing picking + tools |
| 2 | Structured design critic | Only prose today; no axes, scores or evidence model | Solver/verifier metrics |
| 3 | Image → structured spec extraction | No extraction schema or confirmation flow | Vision provider (optional) + field registry |
| 4 | Source-gated calibre/material/component database | 20 thin entries, no calibre records | Curated data + provenance schema |
| 5 | Local RAG over curated/imported documents | Keyword lookup only | Knowledge base |
| 6 | Pareto frontier + multi-objective search | Single scalar ranking only | Optimiser |
| 7 | Assembly-wide interference + tolerance study | Tooth outlines only; worst-case only | Mesh kernel |
| 8 | Section plane + measurement tools | No 3D clipping or measurement | Viewer |
| 9 | Version comparison view + decision log | Text/spec history only | Store + viewer |
| 10 | Spec migration + background jobs + usage ledger | No migration chain, durable jobs or cost model | Store/API |
| 11 | Real STEP / solid kernel | Not present; explicitly unavailable | Optional OCC install |
| 12 | True involute gears | Presentation profiles only | Gear module + tests |
| 13 | Mesh LOD | Fixed tessellation | Viewer/mesh |

---

## 8. Proposed increments (each independently testable)

### Increment A — Component-aware editing + structured critic (priority 1)

**Scope**

1. Add a component-context endpoint/tool: given a selected part/group, return
   the editable spec paths, current values, related checks and locks.
2. Make the chat/client pass the selected component and scope conversational
   edits to it. Reject out-of-scope paths with a clear message.
3. Introduce a deterministic critic module with axes:
   - proportions (case/dial/hand ratios, lug-to-lug vs diameter),
   - dial legibility (index style, contrast, hand/index overlap, date window),
   - aesthetics (finish/material/colour coherence, era consistency),
   - ergonomics (thickness, crown size, lug width, mass band),
   - component consistency (crystal/water resistance, caseback/crown, hands/movement).
4. Each axis returns a score, evidence items with units, issues with severity,
   deterministic fixes, and a provenance label. No LLM-generated numbers.
5. Add `/api/critic` (or extend the agent tool) and a UI critic panel.
6. Tests: scoped edit acceptance/rejection, every axis with known specs,
   determinism, and no mutation from critique.

**Explicit non-goals:** no aesthetic "AI taste" claims, no image extraction yet,
no changes to geometry or export.

**Status — implemented (uncommitted, awaiting user testing).**

| Scope item | Where | Verified by |
| --- | --- | --- |
| Component context endpoint/tool | `watchcad/components.py`, `GET /api/component/context`, `get_component_context` | `ComponentResolutionTests`, `CriticApiTests.test_component_context_endpoint` |
| Selection-scoped conversational edits | `ToolContext.focus`, `_check_focus`, planner focus filtering, focus threading through chat/orchestrator, `component` body field | `ScopedEditTests`, `test_agent_route_passes_the_component_focus` |
| Five-axis deterministic critic | `watchcad/critic.py` (proportions, dial legibility, aesthetics, ergonomics, component consistency) | `CriticTests` (ordering, determinism, no mutation, fixes, locks, focus) |
| Per-axis score, evidence, issues, fixes, provenance | `critique()` report schema | `test_five_axes_are_ordered_and_labelled`, `test_inconsistencies_produce_findings_and_schema_valid_fixes` |
| `/api/critic` + Critic tab | `watchcad/api.py`, `web/index.html`, `web/app.js`, `web/style.css` | `CriticApiTests`, UI smoke `critic` section |
| Tests | `tests/test_increment_a.py` (21 tests), `tests/ui_smoke.js` | `python3 -m unittest discover -s tests` → 119 OK; `node tests/ui_smoke.js` → all checks passed |

**Implementation limitations (must stay documented):**

- The critic is a rubric, not a certificate: it scores modelled data, not a
  physical watch, and it is not a production release gate. Its overall score is
  an equal-weight average of the five axes and is not comparable across
  unrelated design briefs.
- `aesthetics` and `dial_legibility` are explicitly `estimated` heuristics; WCAG
  contrast is computed on modelled colours and does not describe real lighting,
  printing, or applied-index reflectance.
- Mass and ergonomics bands use envelope estimates; they are not measured.
- Component ownership is a curated mapping. A field owned by two plausible
  components resolves to one specific owner, so a focused edit can be refused
  even when it is intuitively related; the refusal always names the owner and
  clearing the selection restores full-design editing.
- Component focus annotates critic findings (`in_focus`); it does not change the
  scores, because every axis still evaluates the whole design.
- Fixes are single-field, schema-valid and deduplicated. They do not explore
  multi-field trade-offs; that is deferred to the optimisation increment.
- Focus is a UX guardrail, not a security boundary: the session API remains
  local and unauthenticated.

### Increment B — Image-to-design workflow (priority 2)

1. Define an `ImageObservation` JSON contract: palette, case shape family, bezel,
   lug style, dial layout, index style, hand style, finish, complications
   visible, confidence per item, and explicit "not determinable".
2. Vision model proposes observations; the server validates enums/ranges and
   converts only defensible observations to candidate patches with
   `source: image` and confidence.
3. A confirmation UI lists proposed fields, before/after values, confidence and
   why each was chosen. Nothing applies until accepted.
4. Tests use a scripted vision response (no network, no real LLM): valid,
   malformed, out-of-range, low-confidence and "unsupported dimension" cases.
5. Document that pixel measurements are not calibrated and are never presented
   as dimensions.

### Increment C — Knowledge base and local RAG (priority 3)

1. Expand the curated dataset with source URLs, retrieval dates and explicit
   unknown fields. Calibre records are only added where a source is supplied.
2. Add a dependency-free BM25-style index over curated documents and imported
   text datasheets; return passages with document IDs, source and offsets.
3. Add `/api/knowledge/search` and a UI search panel with citations.
4. Tests: retrieval ranking, unknown-claim refusal, provenance completeness,
   and no network access.

### Increment D — Pareto optimisation + constraints (priority 4)

1. Evaluate a candidate grid spanning weight, cost, DFM and tolerance changes.
2. Non-dominated sorting with a frontier table, dominated-by links and
   per-objective deltas.
3. Add assembly interference and a seeded tolerance study, labelled `calculated`
   or `simulated`.
4. Hard/soft constraint model; supplier records remain optional and visibly
   unknown when absent.
5. Tests: known frontier fixtures, constraint violation handling, determinism,
   and no automatic application.

### Increment E — Inspection and measurement (priority 5)

1. Section plane with axis/offset/flip and a software-renderer clip fallback.
2. Measurement mode with snapping, distance, angle and clearance; labels and a
   measurement list.
3. Side-by-side version comparison using stored specs.
4. Tests: plane clipping math, measurement math on known geometry, and
   selection/visibility regression.

### Increment F — CAD kernel and manufacturing (priority 6)

1. Kernel adapter interface; keep the mesh backend as the default.
2. Optional OCC backend for STEP/IGES and booleans, only when installed.
3. Deterministic mesh fillet/chamfer approximations where safe; document limits.
4. True involute gear profile option with conjugate-contact tests.
5. BOM/manufacturing docs gain operation, inspection and validation sections.

### Increment G — Reliability (cross-cutting)

- Spec migrations with version fixtures.
- Durable in-process job runner for optimisation/build.
- Provider usage ledger and cost estimates (keys never persisted).
- Deterministic LOD tiers.
- Cross-layer consistency contract tests.

---

## 9. Limitations that will remain after this work

- No claim of production readiness. Calculated geometry is not a validated
  design; bench properties remain `needs_test`.
- A vision model's observations are advisory. Without calibration, photographs
  cannot yield trustworthy dimensions.
- Curated knowledge is only as good as its sources. Missing calibre data stays
  missing rather than guessed.
- STEP requires a real solid kernel; the default install remains mesh-only.
- Structural/FEA, fatigue, escapement dynamics, lubrication, CAM toolpaths and
  material certification remain out of scope unless separately validated.
- The server is a local, unauthenticated studio; session isolation is not
  multi-user security.

---

## 10. Decisions needed before implementation

1. ~~**First increment:** proceed with A (component-aware editing + structured
   critic) as proposed?~~ **Decided: yes. Increment A is implemented and awaiting
   user testing in the working tree.** Remaining questions apply to later
   increments.
2. **Knowledge sources:** do you have manufacturer datasheets or a preferred
   calibre list, or should the calibre database start empty and only accept
   user-supplied sources?
3. **Vision behaviour:** should image extraction require a configured vision
   provider, or also offer a deterministic "palette/proportion only" fallback?
4. **OCC:** is installing OpenCASCADE acceptable as an optional extra, or must
   the project stay strictly zero-dependency?
5. **Background jobs:** is an in-process job runner sufficient, or do you want a
   separate worker process?
