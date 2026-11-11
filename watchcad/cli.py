"""Command line interface.

    python3 -m watchcad new --preset diver -o diver.json
    python3 -m watchcad prompt "38 mm dress watch, manual wind, no date" -o dress.json
    python3 -m watchcad verify dress.json
    python3 -m watchcad build dress.json -o out/dress
    python3 -m watchcad export dress.json --format pdf --out dress.pdf
    python3 -m watchcad serve --open
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from . import exporters, mesh as M
from .build import build, slugify
from .drawing import drawing_set, to_dxf, to_svg, write_drawings, write_pdf
from .nlp import ParseResult, describe, ideas, parse, preset_prompt
from .parts import build_scene
from .schema import Spec, new_spec
from .solver import solve
from .verify import summary, verify


def _value(text: str):
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def _load(path: str) -> Spec:
    try:
        with open(path, encoding="utf-8") as fh:
            return Spec.from_json(fh.read())
    except FileNotFoundError:
        raise SystemExit(f"spec not found: {path}")


def _prepare(spec: Spec):
    solved, report = solve(spec)
    scene = build_scene(solved)
    checks = verify(solved, scene)
    return solved, scene, checks, report


def _write(path: str, data, binary: bool = False) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    mode = "wb" if binary else "w"
    kwargs = {} if binary else {"encoding": "utf-8"}
    with open(path, mode, **kwargs) as fh:
        fh.write(data)


def _print_result(result: ParseResult) -> None:
    print(f"engine: {result.engine}; applied {result.applied} patch(es)")
    if result.notes:
        for note in result.notes:
            print(f"  note: {note}")
    for path in result.skipped:
        print(f"  locked, kept: {path}")
    if result.questions:
        print("\nquestions the prompt did not settle:")
        for q in result.questions:
            print(f"  - {q}")
    print("\n" + result.spec.summary())


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_new(args) -> int:
    if args.preset:
        prompt = preset_prompt(args.preset) or f"{args.preset} watch"
        spec = parse(prompt, name=args.name).spec
    else:
        spec = new_spec(args.name)
    for item in args.set or []:
        if "=" not in item:
            raise SystemExit(f"--set expects path=value, got {item!r}")
        path, value = item.split("=", 1)
        spec.set(path.strip(), _value(value.strip()), tag="user", note="command line")
    solved, _, checks, _ = _prepare(spec)
    text = solved.to_json()
    if args.out:
        _write(args.out, text)
        print(f"wrote {args.out}  ({solved.summary()})")
    else:
        print(text)
    return 1 if any(c["passed"] is False for c in checks) else 0


def cmd_prompt(args) -> int:
    base = _load(args.spec) if args.spec else None
    result = parse(args.prompt, base=base, use_llm=not args.no_llm, name=args.name)
    _print_result(result)
    solved, _, checks, _ = _prepare(result.spec)
    if args.out:
        _write(args.out, solved.to_json())
        print(f"\nwrote {args.out}")
    elif not args.build:
        print()
        print(solved.to_json())
    if args.build:
        target = args.build if args.build is not True else os.path.join("out", slugify(solved.get("meta.name")))
        r = build(solved, out_dir=target, scene=build_scene(solved), checks=checks)
        print(f"\npackage: {r.root}  ({len(r.files)} files)")
    return 1 if any(c["passed"] is False for c in checks) else 0


def cmd_verify(args) -> int:
    spec, _, checks, report = _prepare(_load(args.spec))
    s = summary(checks)
    if args.json:
        print(json.dumps({"summary": s, "checks": checks}, indent=2))
    else:
        print(f"{spec.get('meta.name')}  |  spec {spec.get('derived.checksum')}")
        print(f"{s['calculated']} calculated, {s['estimated']} estimated, "
              f"{s['needs_test']} needs test, {s['fail']} failed, {s['total']} total\n")
        layer = None
        for c in checks:
            if c["layer"] != layer:
                layer = c["layer"]
                print(f"{layer.upper()}")
            mark = "FAIL" if c["passed"] is False else c["verdict"]
            print(f"  [{mark:11s}] {c['id']:28s} {c['detail']}")
            if c.get("fix"):
                fix = c["fix"]
                if "path" in fix:
                    print(f"  {'':13s} fix: {fix['path']} = {fix.get('value')}")
        if report and args.why:
            print("\nDERIVATION")
            for row in report:
                print(f"  {row.get('field')}: {row.get('value')}  ({row.get('why')})")
    return 1 if s["fail"] else 0


def cmd_build(args) -> int:
    spec = _load(args.spec)
    solved, scene, checks, _ = _prepare(spec)
    out = args.out or os.path.join("out", slugify(solved.get("meta.name")))
    result = build(solved, out_dir=out, per_part=not args.no_parts,
                   force=args.force, scene=scene, checks=checks)
    s = result.summary
    print(f"{'reused' if result.reused else 'built'} {result.root}")
    print(f"  {len(result.files)} files, {len(scene['parts'])} parts, "
          f"{scene['triangles']} triangles, {scene['mass_g']:.2f} g")
    print(f"  checks: {s['calculated']} calculated, {s['estimated']} estimated, "
          f"{s['needs_test']} needs test, {s['fail']} failed")
    print(f"  open: {os.path.join(result.root, 'README.md')}")
    return 1 if s["fail"] else 0


def cmd_export(args) -> int:
    spec, _, _, _ = _prepare(_load(args.spec))
    fmt = args.format.lower()
    out = args.out
    if fmt in ("stl", "obj", "gltf"):
        scene = build_scene(spec)
        parts = scene["parts"]
        if fmt == "stl":
            _write(out, M.to_stl(parts), binary=True)
        elif fmt == "obj":
            _write(out, M.to_obj(parts))
        else:
            _write(out, json.dumps(M.to_gltf(parts, slugify(spec.get("meta.name")))))
    elif fmt in ("svg", "dxf", "pdf"):
        canvases = drawing_set(spec)
        if fmt == "svg":
            name = args.view if args.view in canvases else "front"
            _write(out, to_svg(canvases[name]))
        elif fmt == "dxf":
            _write(out, to_dxf(list(canvases.values())))
        else:
            write_pdf(list(canvases.values()), out,
                      title=f"{spec.get('meta.name')} - production drawing",
                      meta={"checksum": spec.get("derived.checksum", "")[:8]})
    elif fmt == "bom":
        _write(out, exporters.bom_csv(spec, build_scene(spec)))
    elif fmt == "spec":
        _write(out, spec.to_json())
    elif fmt == "checks":
        _, scene, checks, _ = _prepare(spec)
        _write(out, json.dumps({"summary": summary(checks), "checks": checks}, indent=2))
    else:
        raise SystemExit(f"unknown format: {fmt}")
    print(f"wrote {out}")
    return 0


def cmd_presets(args) -> int:
    for item in ideas():
        print(f"{item['id']:12s} {item['prompt']}")
    return 0


def cmd_show(args) -> int:
    spec, _, checks, report = _prepare(_load(args.spec))
    print(describe(spec))
    print()
    for row in report:
        print(f"{row.get('field'):28s} {row.get('value')}  ({row.get('why')})")
    return 0


def cmd_serve(args) -> int:
    from . import api
    api.run(args.host, args.port, args.open)
    return 0


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="watchcad",
                                 description="parametric watch design from a spec")
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("new", help="create a new spec")
    p.add_argument("--preset", choices=[i["id"] for i in ideas()])
    p.add_argument("--name", default="Untitled")
    p.add_argument("--set", action="append", metavar="PATH=VALUE")
    p.add_argument("-o", "--out")
    p.set_defaults(func=cmd_new)

    p = sub.add_parser("prompt", help="turn a prompt into spec patches")
    p.add_argument("prompt")
    p.add_argument("--spec", help="existing spec to patch")
    p.add_argument("--name")
    p.add_argument("--no-llm", action="store_true")
    p.add_argument("-o", "--out")
    p.add_argument("--build", nargs="?", const=True, metavar="DIR")
    p.set_defaults(func=cmd_prompt)

    p = sub.add_parser("verify", help="run the layered verification")
    p.add_argument("spec")
    p.add_argument("--json", action="store_true")
    p.add_argument("--why", action="store_true", help="show the derivation report")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("build", help="write the production package")
    p.add_argument("spec")
    p.add_argument("-o", "--out")
    p.add_argument("--no-parts", action="store_true")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("export", help="export one format")
    p.add_argument("spec")
    p.add_argument("--format", required=True,
                   choices=["stl", "obj", "gltf", "svg", "dxf", "pdf", "bom", "spec", "checks"])
    p.add_argument("--view", default="front", choices=["front", "section", "train", "notes"])
    p.add_argument("-o", "--out", required=True)
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("presets", help="list the built-in design prompts")
    p.set_defaults(func=cmd_presets)

    p = sub.add_parser("show", help="describe a spec and its derivation")
    p.add_argument("spec")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("serve", help="start the local web studio")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--open", action="store_true")
    p.set_defaults(func=cmd_serve)
    return ap


def main(argv: list[str] | None = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
