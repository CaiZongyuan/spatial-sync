"""Vendor only the files referenced by Newton 1.7.0's pinned Franka FR3 URDF.

This developer-only tool uses one fixed, trusted upstream revision. Runtime scene loading
uses the bundled files and never invokes this downloader.
"""

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.request import urlopen
from xml.etree import ElementTree

REVISION = "f8fb7abcbeba2318814a74f3eeb02780ad7925d6"
BASE = f"https://raw.githubusercontent.com/newton-physics/newton-assets/{REVISION}/"
PACKAGE = "franka_emika_panda"
URDF = f"{PACKAGE}/urdf/fr3_franka_hand.urdf"
DESTINATION = Path(__file__).resolve().parents[1] / "assets/robots"


def fetch(path):
    with urlopen(BASE + path, timeout=30) as response:
        content = response.read()
    target = DESTINATION / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    return path, {"sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)}


def main():
    _, urdf_record = fetch(URDF)
    root = ElementTree.parse(DESTINATION / URDF).getroot()
    paths = {mesh.attrib["filename"].removeprefix("package://") for mesh in root.iter("mesh")}
    paths |= {f"{PACKAGE}/LICENSE", f"{PACKAGE}/README.md"}
    if any(not path.startswith(PACKAGE + "/") or ".." in Path(path).parts for path in paths):
        raise ValueError("Upstream URDF references a path outside the pinned robot package")
    with ThreadPoolExecutor(max_workers=6) as executor:
        records = dict(executor.map(fetch, sorted(paths)))
    records[URDF] = urdf_record
    manifest = {
        "repository": "https://github.com/newton-physics/newton-assets",
        "revision": REVISION,
        "newtonVersion": "1.7.0",
        "entrypoint": URDF,
        "description": (
            "Official Newton example Franka Research 3: seven revolute arm DOFs and two prismatic fingers"
        ),
        "license": "Apache-2.0; original per-asset LICENSE retained",
        "files": records,
    }
    target = DESTINATION / PACKAGE / "provenance.json"
    target.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"Vendored {len(records)} files, {sum(record['bytes'] for record in records.values())} bytes")


if __name__ == "__main__":
    main()
