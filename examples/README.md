# Example specs

Seven briefs, each parsed by the rule engine, solved and verified. Every one
builds with **0 failing checks**.

| file | design | checks |
|---|---|---|
| `diver.json` | Abyss 42 — steel diver, 300 m, date | 25 calculated · 6 estimated · 8 needs_test |
| `dress.json` | Meridian 38 — manual wind, 48 h, no date | 25 · 5 · 8 |
| `field.json` | Surveyor 38 — bead-blasted field watch | 25 · 5 · 8 |
| `pilot.json` | Navigator 43 — large crown, automatic | 25 · 5 · 8 |
| `chronograph.json` | Circuit 40 — panda dial, two pushers | 25 · 6 · 8 |
| `moonphase.json` | Luna 40 — blue sunburst, moon aperture | 25 · 6 · 8 |
| `quartz.json` | Current 36 — titanium quartz | 16 · 2 · 7 |

Regenerate any of them from its natural-language brief:

```bash
python3 -m watchcad prompt \
  "A 42 mm steel dive watch, 300 m water resistance, black matte dial with applied lume indices, baton hands, sapphire crystal, screw-down caseback, date." \
  --name "Abyss 42" --no-llm -o examples/diver.json
```

Inspect, verify and package one:

```bash
python3 -m watchcad show examples/diver.json
python3 -m watchcad verify examples/diver.json
python3 -m watchcad build examples/diver.json -o out/diver
```

The JSON files include the solved `derived` block, so `show` and `verify` work
without re-parsing the prompt. Edit the `data` values (or use
`python3 -m watchcad prompt … -o`) to make a variant; re-running the solver
recomputes every dependent field. Locked paths are respected.
