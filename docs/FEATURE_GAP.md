# WatchMaker 2.0 — feature-gap report

**Snapshot:** `4f53861` (local `main`, three commits ahead of `origin/main`;
nothing pushed), plus **Increment A and Phases 1–4** in the working tree
(uncommitted). 161 Python tests and the Node UI smoke suite pass on this tree.

**Implementation log:** Increments A and Phases 1–4 are implemented in the
working tree. The scorecard rows for the affected areas are updated to ✅ or ◐
where an important limitation remains. The original gap analysis below is kept
for context; sections marked **Delivered** describe what is now present.

**Delivered in this working tree**

| Phase | What is now end-to-end |
| --- | --- |
| A | Component-aware editing and a deterministic five-axis critic (`components.py`, `critic.py`, `/api/component/context`, `/api/critic`, Critic tab) |
| 1 | Parametric geometry and rendering: central material/finish library, lens/chamfer/arc/knurl primitives, detailed case/dial/hand furniture, scene validation, low/medium/high quality presets, GLB UVs and PBR extras (`materials.py`, `mesh.py`, `parts.py`, `verify.py`) |
| 2 | Source-backed movement database: four manufacturer-published calibres, per-field provenance, append-only corrections, migration, search/catalog/detail/propose/compatibility API and Movement tab (`movements.py`, `schema.py`, `api.py`, `web/app.js`) |
| 3 | Real multi-objective optimisation engine: deterministic candidate field, hard constraints, mixed min/max objectives, Pareto dominance, NSGA-II non-dominated sorting, crowding distance and reproducibility metadata (`optimise.py`) |
| 4 | Optimisation Lab: selectable scatter axes, frontier/dominated/infeasible split, candidate inspection, baseline diff, constraints, 3D preview, explicit confirm-apply, new-version apply and undo/rollback (`api.py`, `web/app.js`, `web/index.html`, `web/viewer3d.js`) |

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
| Parametric geometry detail | ◐ | Case/bezel/lug/crown/crystal/caseback and dial/hand furniture are spec-driven mesh approximations; no B-rep booleans or true threads |
| PBR rendering and quality presets | ✅ | UV/PBR GLB, IBL/shadows, low/medium/high tiers, texture reuse and lazy on-demand render; no dynamic LOD |
| Geometry/scene validation | ✅ | Missing/disconnected parts, positions, collisions, invalid dimensions, references and generation failures; not a manufacturing release |
| Version history / snapshots / undo / redo | ✅ | Checkpoints, labelled versions, candidate apply as a new version, undo/rollback; no full side-by-side visual comparison UI |
| Render/engineering consistency | ◐ | One scene feeds glTF, the Three.js viewer and the canvas fallback; GLB UV/PBR and scene-verification tests exist, but no pixel-equivalence test |
| Knowledge base: movements/calibres | ✅ | Four manufacturer-published calibres with per-field provenance; deliberately small source-gated set (`movements.py`, Movement tab) |
| Knowledge base: materials/processes/components | ◐ | Central PBR/density library plus 20 curated entries; process data and real material certificates remain thin |
| RAG / source-grounded retrieval | ◐ | Curated lookup plus optional live search with URL/timestamp; no chunked corpus or embeddings |
| Multi-objective optimisation | ✅ | Five deterministic objectives, hard constraints, reproducible seed/signature and real solver+mesh+verifier per candidate |
| Pareto frontier | ✅ | Pareto dominance, NSGA-II non-dominated sorting, crowding distance, selectable scatter axes and frontier/dominated split |
| Tolerance stack / DFM / interference | ◐ | Worst-case stack, DFM, tooth outlines and scene-level missing/orphan/envelope checks; no assembly-wide mesh intersection or supplier constraints |
| Structural constraints | ✗ | No FEA, stress, deflection or fatigue model |
| Movement compatibility | ✅ | Structured PASS/WARNING/FAIL/UNKNOWN checks for diameter, height, mounting, stem/crown, dial, hands, date and caseback; UNKNOWN never silently passes |
| True STEP export | ✗ | Status explicitly reports STEP unavailable without an OCC-style kernel |
| Solid booleans / fillets / chamfers / threads | ◐ | Deterministic mesh chamfers, lens edges and knurls are generated; no B-rep booleans, drafts or real screw threads |
| True involute gears | ✗ | Exact ratios, presentation-grade cycloidal profiles |
| BOM / manufacturing docs | ✅ | BOM, process notes, test plan, drawings, manifest hashes |
| Versioned spec / migration | ✅ | `SCHEMA_VERSION = 3` with a pure additive migration chain and history entry |
| Background jobs | ◐ | Threaded HTTP server, agent cancel/timeout and token-guarded UI runs; optimisation is still synchronous in-request with no durable job record |
| Provider usage / cost | ◐ | Token usage is reported when the provider returns it; no price table, aggregation or persistence |
| Mesh LOD / simplification | ◐ | Low/medium/high geometry presets, capped texture sizes with reuse and per-quality scene cache; no distance-based decimation |
| Session / project isolation | ✅ | Per-session studio, conversation-scoped design sessions, session-scoped BYOK keys |
| Cross-layer regression tests | ◐ | 161 Python tests plus UI smoke covering spec→scene→GLB→API→UI; no solid-CAD equivalence assertion |

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
- **Delivered — structured critic** (`watchcad/critic.py`, `/api/critic`, Critic
  tab): five deterministic axes with scores, evidence, findings, schema-valid
  safe fixes and provenance. LLM output never supplies the numbers.
- **Delivered — selection-scoped editing** (`watchcad/components.py`): the
  selected part resolves to writable fields, locks, checks and scene parts;
  chat and agent mutation refuse out-of-scope fields and name the owning
  component in the refusal.

### 1.2 What is missing

- **No image → structured specification workflow.** There is no extraction
  schema, no calibrated proportion estimate, no confidence/provenance fields,
  and no "accept these extracted fields" review step. Vision output is prose in
  the conversation, not spec patches.
- **No sketch/photo decomposition.** Case shape, bezel, lugs, dial layout,
  indices, hands, colours and finish are not turned into candidate field values
  with uncertainty.
- **No image-derived design lineage.** Uploaded references are not stored as
  design sources with retrieval dates and confidence.
- **No per-component overrides.** Selection scopes *which global fields may be
  edited*, but a dial-specific colour that differs from the global dial colour
  is not modelled.
- **Delivered (was missing): structured critic.** Five explicit axes now return
  scores, evidence, issues, fixes and provenance from the solved spec, scene and
  verifier.
- **Delivered (was missing): selection-scoped editing.** The selected component
  is injected into chat/agent requests and mutating tools refuse out-of-scope
  fields.

### 1.3 Genuinely new / delivered here

- A deterministic **image-brief extractor** *(not implemented here)*: vision
  model returns a bounded JSON observation (no invented dimensions), the server
  validates it against the field registry, converts only defensible observations
  into patches, attaches confidence and `source: image`, and asks the user to
  confirm essentials.
- A **structured critic** *(delivered)* with named axes and deterministic
  evidence collectors, returning scores, issues, fixes and provenance. The LLM
  may explain the report; it must not invent the numbers.
- **Selection-scoped editing** *(delivered)*: selected component → allowed
  fields, current values, relevant checks, and a validated conversational edit
  path.

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
- The same scene payload drives the glTF export, the Three.js viewer and the
  no-WebGL canvas fallback, so parts, colours, bounds and mass come from one
  source.
- **Delivered — render presets.** Low/medium/high tiers change tessellation,
  shadow-map size, device pixel ratio and procedural texture size. The server
  caches scenes per quality; the viewer caps pixel ratio, reuses generated
  textures and renders on demand. Dimensions and verification are identical
  across presets.
- **Delivered — candidate preview.** Session-scoped, short-lived preview tokens
  let `/api/model.glb` and `/api/scene` render a candidate without mutating the
  live studio.

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
- **Automated render/geometry consistency test is still partial.** New tests
  assert quality-independent dimensions/verification, part presence, layer order,
  GLB UVs and PBR extras; no pixel-level or solid-CAD equivalence test exists.
- **No live section plane, measurement tools, side-by-side history comparison or
  per-part visibility.** These remain the largest 3D-workspace gaps.

### 2.3 Genuinely new (not implemented here)

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
- **Delivered — source-backed movement database** (`watchcad/movements.py`):
  ETA 2824-2, Miyota 9015, Sellita SW200-1 and Seiko NH35A. Each field carries
  `verified`, `secondary`, `estimated` or `unknown` provenance; unpublished
  values stay `null`/UNKNOWN. Records are append-only corrected, migrated and
  validated, and live only on the server (`/api/movements*`, Movement tab).

### 3.2 What is missing

- **Movement coverage is deliberately small.** Four manufacturer-published
  records are enough to exercise sourcing, provenance and compatibility, not to
  serve as a general calibre catalogue. New records must cite a source.
- **Thin material properties.** Density and hardness only; no elastic modulus,
  yield strength, thermal expansion, machinability or supplier form data.
- **Thin process data.** No feeds/speeds, tolerances by process, minimum feature
  size, surface finish capability, or cost drivers.
- **No standard fasteners/components catalogue.** No screw standards, spring
  bars, gaskets, O-ring sizes, crystals by diameter/thickness, crowns/tubes,
  caseback threads or sealing systems.
- **No complete assembly/engineering constraint model.** Movement compatibility
  now adds structured mounting, stem/crown, hand, date and caseback checks, but
  this is still not a general constraint solver or a physical release gate.
- **No RAG.** Lookup is keyword matching over a hand-written list. There is no
  document corpus, chunking, embedding/index, hybrid retrieval, reranking or
  citation-anchored answer construction.
- **General curated-entry provenance is still thin.** Most `knowledge.py`
  entries have no `source_url` or `retrieved_at`; the default note honestly says
  no datasheet was fetched. The movement database does not inherit that gap.

### 3.3 Genuinely new / delivered here

- A source-gated calibre dataset *(delivered, small set)* with explicit
  `verified | secondary | estimated | unknown` status, source URLs, retrieval
  date, and "unknown" fields that are never filled by guesswork.
- Expanded material/process/component tables with units and test methods
  *(not implemented here)*.
- A local, dependency-free retrieval layer (token index + BM25-style scoring)
  over curated documents and user-imported datasheets, returning passages with
  source spans *(not implemented here)*.
- A knowledge answer builder that refuses to answer when no source supports the
  claim *(not implemented here)*.

---

## 4. Advanced engineering optimisation

### 4.1 What already exists

- `watchcad/optimise.py`: five objectives — weight, cost, manufacturability,
  tolerance, assembly. Each candidate is a real set of spec changes; the solver,
  mesh builder and verifier run per candidate. Nothing is applied automatically.
- Metrics include mass, material/process cost, part and complication counts, DFM
  failures, tolerance failures, stack margin and failing checks, each with a
  provenance label (`calculated` / `estimated` / `needs_test`).
- Hard constraints (schema validity, locks, solver success, verification and a
  usable material density) are reported separately from objectives. The engine
  uses mixed min/max objective vectors, Pareto dominance, NSGA-II
  non-dominated sorting and crowding distance. The single-objective `winner`
  is a recommendation from the finite probe set, reported separately from the
  Pareto front; no global-optimality claim is made.
- A deterministic seed/signature records baseline checksum, objective, axes,
  budget and generator version. Every candidate reports baseline deltas,
  constraints, verification and provenance.
- Verifier already covers: movement/dial fit, stack and hand clearance, gear
  centre distances and outline interference, tooth conflicts, kinematics, power
  budget/reserve, worst-case tolerance stack, DFM (water resistance, lug width,
  hand/crystal clearance) and physical `needs_test` items.
- Variants are applied through the validated pipeline, with locks respected.

### 4.2 What is missing

- **Global or continuous search.** The field is a finite, deterministic probe
  set; there is no adaptive sampling, knee-point selection or guarantee that a
  better design lies outside the probes.
- **Structural constraints.** No stress, deflection, fatigue, impact or FEA.
  "Structural" is currently limited to geometric fit and interference.
- **Assembly-wide interference.** Tooth outlines are checked, but no general
  part-vs-part mesh intersection or swept-volume check for hands, rotor,
  caseback, crown/stem and complications.
- **Supplier/process constraints.** Cost tables are global constants; no supplier
  capability, minimum order, tolerance capability, lead time or process routing.
- **Tolerance stack sophistication.** Worst-case arithmetic only; no RSS/statistical
  stack, no sensitivity ranking, no Monte-Carlo, no measured distributions.
- **Movement rotor clearance and some hand-tube details.** The delivered
  compatibility report covers mounted, stem/crown, dial, hand-height, date and
  caseback checks where the source publishes the value, but not every calibre
  detail or swept rotor envelope.
- **Batch/background optimisation.** The endpoint runs synchronously and holds
  the studio lock; the UI guards against stale results with a request token, but
  there is no job queue, progress stream or resumable run.

### 4.3 Genuinely new / delivered here

- Pareto analysis *(delivered)* over the existing candidate metrics:
  non-dominated sorting, crowding distance, frontier/dominated split,
  per-objective deltas, explicit dominance links and selectable scatter axes.
- A constraint model *(delivered)* with blocking hard constraints and
  non-blocking caveats; unknown data stays UNKNOWN rather than passing.
- Assembly interference using deterministic mesh/AABB + triangle tests, reported
  as `calculated` with tolerance assumptions stated *(not implemented here)*.
- Supplier capability records and pass/fail evidence *(not implemented here)*.
- Optional Monte-Carlo tolerance study with a fixed seed and documented
  distributions *(not implemented here)*.

---

## 5. Manufacturing and CAD

### 5.1 What already exists

- Mesh kernel: revolve, tube, frustum, dome, torus, extrude, rounded slab, sweep,
  merge, outward winding and signed volume, plus delivered lens, chamfered disc,
  arc ring and knurled-disc primitives with UVs.
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
- **True fillets, drafts and screw threads.** Deterministic mesh chamfers,
  lens edges and knurls are now generated, but they are mesh approximations;
  there is no edge-blending kernel or helical thread form.
- **True involute gears.** Ratios are exact, but profiles are presentation-grade
  cycloidal approximations; no conjugate action, backlash, contact ratio or
  stress.
- **Manufacturing documentation depth.** No operation sheets with setup,
  fixtures, tooling, inspection gauges, tolerance capability or CAM toolpaths.
- **Validation evidence.** No material certificates, no first-article
  inspection, no bench data ingestion.

### 5.3 Genuinely new / delivered here

- A kernel adapter interface with a dependency-free mesh backend and an optional
  OCC backend *(not implemented here)*. STEP export only when a real kernel is
  installed; otherwise the existing honest "unavailable" status remains.
- Mesh chamfer/lens/arc/knurl improvements *(delivered)* where they can be done
  deterministically without a B-rep, labelled as mesh approximations.
- A gear module that can switch between the existing presentation profile and a
  true involute profile, with a test that verifies conjugate contact at the pitch
  point and documents remaining limits *(not implemented here)*.

---

## 6. Project reliability

### 6.1 What already exists

- `meta.version` increments on writes; exports carry `SCHEMA_VERSION = 3`.
  `migrate_spec_dict()` is a pure additive migration that fills new fields from
  current defaults and records a history entry without guessing existing values.
- SQLite store: conversations, messages, tool events, summaries, latest design
  per session, conversation→design references. DB access stays in the server,
  not the frontend.
- Per-session studio and conversation-scoped design sessions; new conversations
  clone then diverge; session-scoped BYOK keys live only in process memory.
- Agent run control: cooperative cancellation, wall-clock budget, bounded
  repair iterations, streaming timeline events.
- Token usage is captured from provider responses and surfaced in run results.
- Spec, solver, geometry, movements, exports, API, optimisation and agent paths
  have **161 Python tests**; the UI has a headless smoke suite.
- **Delivered — quality tiers.** Low/medium/high geometry presets, per-quality
  scene cache, capped/reused procedural textures and on-demand rendering. These
  are performance hints, not engineering-accuracy settings.

### 6.2 What is missing

- **Historical fixtures for every old schema version.** The additive migration
  chain is implemented and tested for legacy/current documents, but only the
  paths needed by this repository are covered.
- **Decision log.** Design references record checksums and labels, but not the
  rationale, alternatives considered, or who/what decided. Agent memory is
  conversation-scoped.
- **Background execution for optimisation/build.** Long work is synchronous in
  the HTTP request. The UI uses a request token to ignore stale results and the
  agent has cancellation/streaming, but there is no durable job record, progress,
  retry or resume for the optimisation lab.
- **Provider cost tracking.** No per-provider price table, per-run cost,
  monthly aggregation, budget guard, or persisted usage ledger.
- **Distance-based LOD.** Deterministic quality tiers exist, but there is no
  runtime decimation or camera-distance switching.
- **Full cross-layer regression tests.** Tests cover spec→solver→scene→GLB→API→UI
  behaviour, including quality invariance and movement provenance; no single
  test asserts agent prose, drawing labels and glTF node names all agree.
- **User isolation/authentication.** Session isolation is not security; the
  server is localhost-only and unauthenticated by design.

### 6.3 Genuinely new / delivered here

- Versioned spec migrations with additive behaviour and a recorded migration
  history *(delivered; exhaustive historical fixtures not added)*.
- A job runner (in-process, dependency-free) with status, progress, cancel and
  result retrieval, used by optimisation and package builds *(not implemented
  here)*.
- A provider usage ledger: tokens, model, endpoint, estimated cost, timestamp;
  keys never persisted. Cost tables are explicit and editable *(not implemented
  here)*.
- Mesh quality tiers generated deterministically from the same geometry, with a
  UI quality control and triangle-count readout *(delivered; no dynamic LOD)*.
- A "consistency contract" test: one solved spec → agent summary fields, scene
  parts/bounds, glTF node names and drawing labels must agree *(partial)*.

---

## 7. Genuinely new work, ranked by the requested priority

| Rank | Feature | Why it is new | Status here | Dependency |
| --- | --- | --- | --- | --- |
| 1 | Selection-scoped component editing | Selected part is not an edit target; no per-component overrides | Delivered (global fields only) | Existing picking + tools |
| 2 | Structured design critic | Only prose today; no axes, scores or evidence model | Delivered | Solver/verifier metrics |
| 3 | Image → structured spec extraction | No extraction schema or confirmation flow | Not implemented | Vision provider (optional) + field registry |
| 4 | Source-gated calibre database | 20 thin entries, no calibre records | Delivered (four calibres) | Curated data + provenance schema |
| 5 | Local RAG over curated/imported documents | Keyword lookup only | Not implemented | Knowledge base |
| 6 | Pareto frontier + multi-objective search | Single scalar ranking only | Delivered (finite probe field) | Optimiser |
| 7 | Assembly-wide interference + tolerance study | Tooth outlines only; worst-case only | Partial (scene envelope/layer checks) | Mesh kernel |
| 8 | Section plane + measurement tools | No 3D clipping or measurement | Not implemented | Viewer |
| 9 | Version comparison view + decision log | Text/spec history only | Partial (candidate diff/undo) | Store + viewer |
| 10 | Spec migration + background jobs + usage ledger | No migration chain, durable jobs or cost model | Migration delivered; jobs/ledger not | Store/API |
| 11 | Real STEP / solid kernel | Not present; explicitly unavailable | Not implemented | Optional OCC install |
| 12 | True involute gears | Presentation profiles only | Not implemented | Gear module + tests |
| 13 | Mesh quality tiers / LOD | Fixed tessellation | Delivered (quality tiers); no dynamic LOD | Viewer/mesh |

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
| Tests | `tests/test_increment_a.py` (21 tests), `tests/ui_smoke.js` | `python3 -m unittest discover -s tests` → 161 OK across the whole suite; `node tests/ui_smoke.js` → all checks passed |

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

**Status — not implemented here.** Images are still conversational/vision context,
not validated spec patches. Keep the plan below as the remaining scope.

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

**Status — partial.** The source-gated calibre database is delivered;
BM25-style local retrieval, `/api/knowledge/search` and a citation UI are not.

1. Expand the curated dataset with source URLs, retrieval dates and explicit
   unknown fields. Calibre records are only added where a source is supplied.
2. Add a dependency-free BM25-style index over curated documents and imported
   text datasheets; return passages with document IDs, source and offsets.
3. Add `/api/knowledge/search` and a UI search panel with citations.
4. Tests: retrieval ranking, unknown-claim refusal, provenance completeness,
   and no network access.

### Increment D — Pareto optimisation + constraints (priority 4)

**Status — partly implemented (uncommitted).** Pareto sorting, hard constraints,
candidate inspection/diff/preview/apply/undo, determinism tests and the finite
objective grid are delivered. Assembly-wide interference, supplier capability
records and Monte-Carlo tolerance studies are not.

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

**Status — not implemented here.** Candidate inspection is delivered in the
optimisation lab, but 3-D sectioning and measurement tools are not.

1. Section plane with axis/offset/flip and a software-renderer clip fallback.
2. Measurement mode with snapping, distance, angle and clearance; labels and a
   measurement list.
3. Side-by-side version comparison using stored specs.
4. Tests: plane clipping math, measurement math on known geometry, and
   selection/visibility regression.

### Increment F — CAD kernel and manufacturing (priority 6)

**Status — partial.** Deterministic mesh chamfer/lens/arc/knurl improvements are
delivered. The optional solid kernel, STEP/IGES and involute gears are not.

1. Kernel adapter interface; keep the mesh backend as the default.
2. Optional OCC backend for STEP/IGES and booleans, only when installed.
3. Deterministic mesh fillet/chamfer approximations where safe; document limits.
4. True involute gear profile option with conjugate-contact tests.
5. BOM/manufacturing docs gain operation, inspection and validation sections.

### Increment G — Reliability (cross-cutting)

**Status — partial.** Schema v3 additive migration and deterministic quality tiers
are delivered; durable jobs, provider usage ledger and an exhaustive consistency
contract are not.

- Spec migrations with version fixtures.
- Durable in-process job runner for optimisation/build.
- Provider usage ledger and cost estimates (keys never persisted).
- Deterministic LOD tiers.
- Cross-layer consistency contract tests.

---

## 9. Limitations that will remain after this work

- No claim of production readiness. Mesh geometry, scene validation and quality
  tiers do not constitute a validated solid design; bench properties remain
  `needs_test`.
- Visual approximation, parametric geometry and validated solid geometry are
  distinct. Chamfers/lenses/knurls are deterministic mesh approximations, not
  B-rep features; there is no STEP/IGES kernel, booleans, draft or true thread.
- The optimisation field is finite and deterministic. Pareto ranking and the
  recommendation are correct for the probed candidates only; no global
  optimality, continuous search or physical validation is claimed.
- Movement coverage is four source-backed calibres with per-field provenance.
  Missing calibre data stays UNKNOWN, and compatibility results are geometric
  checks with stated assumptions, not a manufacturing release.
- No assembly-wide mesh interference, dynamic distance-based LOD, durable
  background job queue/cancellation, FEA/Monte-Carlo/general tolerance study,
  supplier capability data or local RAG corpus is present.
- A vision model's observations are advisory. Without calibration, photographs
  cannot yield trustworthy dimensions; image-to-spec extraction is not
  implemented.
- Material densities/finishes are presentation/estimate data, not certificates;
  structural/FEA, fatigue, escapement dynamics, lubrication, CAM toolpaths and
  material certification remain out of scope unless separately validated.
- The server is a local, unauthenticated studio; session isolation is not
  multi-user security. BYOK keys stay in process memory and never enter logs.

---

## 10. Decisions needed before implementation

1. ~~**First increment:** proceed with A (component-aware editing + structured
   critic) as proposed?~~ **Decided: yes. Increment A is implemented and awaiting
   user testing in the working tree.** Remaining questions apply to later
   increments.
2. **Knowledge sources:** four manufacturer-sourced calibres are now included.
   Should expansion keep the same source-gated rule (recommended), and which
   additional calibres or datasheets should be prioritised?
3. **Vision behaviour:** should image extraction require a configured vision
   provider, or also offer a deterministic "palette/proportion only" fallback?
4. **OCC:** is installing OpenCASCADE acceptable as an optional extra, or must
   the project stay strictly zero-dependency?
5. **Background jobs:** is an in-process job runner sufficient, or do you want a
   separate worker process?
