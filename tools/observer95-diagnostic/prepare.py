"""Readiness and exact diagnostic patch application; never starts a service."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

packet = Path(__file__).resolve().parent
source = "1ded8b0a5ef39f5978f808f54eb6bcae177ed9c6"
tree = "e19ebb3d8f7927b5cabc82295aecbb94869215a0"

if sys.argv[1] == "apply":
    actual = subprocess.check_output(["git", "rev-parse", "HEAD", "HEAD^{tree}"], text=True).splitlines()
    if actual != [source, tree]:
        raise SystemExit("READINESS FAILURE: source head/tree mismatch")
    subprocess.run(["git", "diff", "--exit-code"], check=True)
    subprocess.run(["git", "diff", "--cached", "--exit-code"], check=True)
    patch = packet / "instrument.patch"
    subprocess.run(["git", "apply", "--check", str(patch)], check=True)
    subprocess.run(["git", "apply", str(patch)], check=True)
    shutil.copyfile(packet / "fixture.ts", "tests/e2e/observer95-diagnostic.ts")
    output = Path(".scratch/observer95")
    output.mkdir(parents=True, exist_ok=True)
    receipt = {"head": source, "tree": tree, "packet": {
        name: hashlib.sha256((packet / name).read_bytes()).hexdigest()
        for name in ("fixture.ts", "instrument.patch", "fixed18.json", "run.py")
    }}
    (output / "source.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    print(json.dumps(receipt))
elif sys.argv[1] == "verify":
    report = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    if report.get("errors"):
        raise SystemExit("READINESS FAILURE: collection errors")
    actual = []
    def visit(suites):
        for suite in suites:
            actual.extend({"file": spec["file"], "title": spec["title"]} for spec in suite.get("specs", []))
            visit(suite.get("suites", []))
    visit(report["suites"])
    if actual != json.loads((packet / "fixed18.json").read_text(encoding="utf-8")):
        raise SystemExit("READINESS FAILURE: original 18 case IDs/order mismatch")
    print("Exact original 18 cases/order verified; no execution performed")
else:
    raise SystemExit("Expected apply or verify")
