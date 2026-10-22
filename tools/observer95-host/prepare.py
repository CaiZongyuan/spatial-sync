"""Readiness and exact diagnostic patch application; never starts a service."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

packet = Path(__file__).resolve().parent
source = "192fc4b465012a34ad95fc4c6ef460da322436a3"
tree = "bfe2374c28551edebee0655a0c2045b82e8d75fd"

if sys.argv[1] == "apply":
    actual = subprocess.check_output(["git", "rev-parse", "HEAD", "HEAD^{tree}"], text=True).splitlines()
    if actual != [source, tree]:
        raise SystemExit("READINESS FAILURE: source head/tree mismatch")
    subprocess.run(["git", "diff", "--exit-code"], check=True)
    subprocess.run(["git", "diff", "--cached", "--exit-code"], check=True)
    patch = packet / "instrument.patch"
    subprocess.run(["git", "apply", "--check", str(patch)], check=True)
    subprocess.run(["git", "apply", str(patch)], check=True)
    shutil.copyfile(packet / "fixture.ts", "tests/e2e/observer95-host.ts")
    shutil.copyfile(packet / "pins.json", "tests/e2e/observer95-host-pins.json")
    output = Path(".scratch/observer95-host")
    output.mkdir(parents=True, exist_ok=True)
    receipt = {"head": source, "tree": tree, "packet": {
        name: hashlib.sha256((packet / name).read_bytes()).hexdigest()
        for name in ("fixture.ts", "instrument.patch", "fixed18.json", "run.py", "pins.json")
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
