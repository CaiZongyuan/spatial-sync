"""Small authored reference packages for public template tests."""

import hashlib
import io
import json
import tarfile

import yaml

from packages.library.common import BUILTIN_ROOT


def upload(client, files):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        for name, value in sorted(files.items()):
            member = tarfile.TarInfo(name)
            member.size = len(value)
            archive.addfile(member, io.BytesIO(value))
    return client.post(
        "/api/v1/library/imports", content=stream.getvalue(), headers={"Content-Type": "application/x-tar"}
    )


def reference_files():
    metadata = {
        "id": "device/slice-reference-centrifuge",
        "kind": "device",
        "title": "Binding reference centrifuge",
        "description": "Synthetic housing proxy; collision, mass and license are not supplied.",
        "stage": "asset.usda",
        "implements": [
            {
                "type": "centrifuge/v1",
                "programs": {"synthetic": "centrifuge.virtual", "newton": "centrifuge.newton"},
                "config": {"slotSites": [f"Rotor/Slot{i}" for i in range(1, 5)], "visualRpmCap": 55},
            }
        ],
    }
    files = {
        "asset.yaml": yaml.safe_dump(metadata).encode(),
        "asset.usda": b"""#usda 1.0
(defaultPrim = "Asset"
 metersPerUnit = 1
 upAxis = "Z")
def Xform "Asset" {
 def Cube "HousingProxy" {
  double size = 1
  float3 xformOp:scale = (0.44, 0.44, 0.1)
  uniform token[] xformOpOrder = ["xformOp:scale"]
 }
 def Xform "Rotor" {
  def Xform "Slot1" {}
  def Xform "Slot2" {}
  def Xform "Slot3" {}
  def Xform "Slot4" {}
 }
}
""",
    }
    files["source/geometry.json"] = json.dumps(
        {
            "source": "packages/sim_runtime/synthetic_lab.py",
            "sourceRevision": "dedacd59a4aa0f13ba32a35e333a2d0922538e55",
            "entityKey": "/World/Lab/Centrifuge/Housing",
            "localPose": {"p": [0, 0, 0], "q": [0, 0, 0, 1]},
            "primitive": {"type": "box", "halfExtents": [0.22, 0.22, 0.05]},
            "scope": "Housing proxy only; station names are declarations, not validated physical placement.",
        }
    ).encode()
    files["provenance.json"] = json.dumps(
        {
            "files": {
                name: {"sha256": hashlib.sha256(value).hexdigest(), "bytes": len(value)}
                for name, value in files.items()
            }
        }
    ).encode()
    return files


def scene_files():
    path = BUILTIN_ROOT / "scenes/lab-demo/2"
    files = {name: (path / name).read_bytes() for name in ("scene.yaml", "devices.yaml", "locations.yaml")}
    metadata = yaml.safe_load(files["scene.yaml"])
    metadata["name"] = "template-slice"
    declarations = yaml.safe_load(files["devices.yaml"])
    declarations["scene"] = metadata["name"]
    centrifuge = next(
        binding for binding in declarations["entities"] if binding.get("type") == "centrifuge/v1"
    )
    centrifuge["template"] = "device/slice-reference-centrifuge@1"
    del centrifuge["type"], centrifuge["program"], centrifuge["config"]["slotSites"]
    centrifuge["config"]["visualRpmCap"] = 61
    centrifuge["alerts"] = {
        "slice-running": {
            "when": {"property": "phase", "equals": "running"},
            "severity": "info",
            "message": "Template is running",
        }
    }
    files["scene.yaml"] = yaml.safe_dump(metadata).encode()
    files["devices.yaml"] = yaml.safe_dump(declarations).encode()
    return files, declarations
