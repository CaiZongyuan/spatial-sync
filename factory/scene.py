"""One command for the pure Scene Factory lane (no Blender, no gateway).

    python factory/scene.py request --plan factory/plans/tube-rack-demo-v1.yaml \
      --out .scratch/sf3/blender/request-v1.json
    python factory/scene.py build --plan factory/plans/tube-rack-demo-v1.yaml \
      --out .scratch/sf3/factory/v1 --layout .scratch/sf3/blender/layout-v1.json
    python factory/scene.py demo --out .scratch/sf3/factory
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from factory.authoring import (  # noqa: E402
    BUILTIN_LIBRARY,
    PlanError,
    authoring_request,
    build,
    load_plan,
    request_digest,
)


def _roots(values):
    return [Path(item) for item in values] if values else [BUILTIN_LIBRARY]


def _write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _layout(path):
    if path is None:
        return None
    return json.loads(Path(path).read_text(encoding="utf-8"))


def command_request(args):
    plan = load_plan(args.plan)
    request = authoring_request(plan, library_roots=_roots(args.library))
    request["requestSha256"] = request_digest(request)
    _write_json(args.out, request)
    print(json.dumps({"request": str(args.out), "requestSha256": request["requestSha256"]}, indent=2))
    return 0


def command_build(args):
    plan = load_plan(args.plan)
    result = build(
        plan,
        args.out,
        library_roots=_roots(args.library),
        layout=_layout(args.layout),
    )
    print(json.dumps(result, indent=2))
    return 0


def command_demo(args):
    plans = Path(__file__).resolve().parent / "plans"
    outputs = {}
    for name in ("tube-rack-demo-v1", "tube-rack-demo-v2", "tube-rack-reuse"):
        plan = load_plan(plans / f"{name}.yaml")
        result = build(plan, Path(args.out) / name, library_roots=_roots(args.library))
        outputs[name] = result
    print(json.dumps({"built": outputs}, indent=2))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    request = commands.add_parser("request", help="Write the Blender authoring request")
    request.add_argument("--plan", required=True)
    request.add_argument("--out", required=True)
    request.add_argument("--library", action="append")
    build_parser = commands.add_parser("build", help="Build packages and a scene recipe")
    build_parser.add_argument("--plan", required=True)
    build_parser.add_argument("--out", required=True)
    build_parser.add_argument("--layout", help="Layout manifest emitted from Blender")
    build_parser.add_argument("--library", action="append")
    demo = commands.add_parser("demo", help="Build both rack versions and the reuse scene")
    demo.add_argument("--out", required=True)
    demo.add_argument("--library", action="append")
    args = parser.parse_args(argv)
    try:
        if args.command == "request":
            return command_request(args)
        if args.command == "build":
            return command_build(args)
        return command_demo(args)
    except (PlanError, ValueError, OSError) as exc:
        print(json.dumps({"error": type(exc).__name__, "message": str(exc)}, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
