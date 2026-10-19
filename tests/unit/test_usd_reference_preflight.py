"""A real SDK boundary oracle: unsafe arcs may be parsed, never composed."""

import pytest
from pxr import Sdf, Usd

from packages.library.common import content_hash
from packages.library.structural import validate


@pytest.mark.parametrize("arc", ["references", "payload", "sublayers"])
@pytest.mark.parametrize("reference_kind", ["absolute", "escaping-relative", "remote"])
def test_rejected_external_arcs_never_reach_usd_stage_composition(tmp_path, monkeypatch, arc, reference_kind):
    package = tmp_path / "package"
    package.mkdir()
    outside = tmp_path / "outside.usda"
    outside.write_text('#usda 1.0\ndef Xform "Secret" {}\n', encoding="utf-8")
    path = {
        "absolute": outside.as_posix(),
        "escaping-relative": "../outside.usda",
        "remote": "https://reviewer.invalid/outside.usda",
    }[reference_kind]
    declaration = f"subLayers = [@{path}@]" if arc == "sublayers" else ""
    body = f'def Xform "External" (prepend {arc} = @{path}@) {{}}' if arc != "sublayers" else ""
    stage = package / "stage.usda"
    stage.write_text(
        f'#usda 1.0\n(\n metersPerUnit = 1\n upAxis = "Z"\n {declaration}\n)\n'
        f'def Xform "World" {{\n def Xform "Lab" {{\n {body}\n }}\n}}\n',
        encoding="utf-8",
    )
    attempts, opened = [], []
    real_layer_open = Sdf.Layer.FindOrOpen

    def observed_layer_open(filename, *args, **kwargs):
        opened.append(filename)
        return real_layer_open(filename, *args, **kwargs)

    def forbidden_composition(*args, **kwargs):
        attempts.append(args)
        raise RuntimeError("Composition must not begin after a rejected external arc")

    monkeypatch.setattr(Usd.Stage, "Open", forbidden_composition)
    monkeypatch.setattr(Sdf.Layer, "FindOrOpen", observed_layer_open)
    report = validate(
        {
            "kind": "scene",
            "subject": "scene:preflight-fixture@1",
            "metadata": {"stage": "stage.usda"},
            "assets": [],
            "types": {},
            "programs": {},
            "package": {"path": str(package), "files": {"stage.usda": str(stage)}},
        }
    )
    failure = next(check for check in report["checks"] if check["id"] == "S7")
    assert failure["status"] == "fail" and any(
        message in failure["message"] for message in ("host paths", "escapes")
    )
    assert attempts == []
    assert opened == [str(stage)]


@pytest.mark.parametrize("arc", ["references", "payload", "sublayers"])
@pytest.mark.parametrize("reference_kind", ["package", "uppercase", "member", "indexed", "unsupported"])
def test_unsupported_composition_formats_are_rejected_before_sdk_composition(
    tmp_path, monkeypatch, arc, reference_kind
):
    package = tmp_path / "package"
    asset = tmp_path / "asset"
    package.mkdir()
    asset.mkdir()
    filename = {
        "package": "bundle.usdz",
        "uppercase": "bundle.USDZ",
        "member": "bundle.usdz",
        "indexed": "bundle.USDZ",
        "unsupported": "bundle.obj",
    }[reference_kind]
    target = (asset if reference_kind == "indexed" else package) / filename
    target.write_bytes(b"Unsupported composition container must never be opened")
    reference = (
        f"library:assets/environment/archive-fixture/1/{filename}"
        if reference_kind == "indexed"
        else f"{filename}[root.usda]"
        if reference_kind == "member"
        else filename
    )
    declaration = f"subLayers = [@{reference}@]" if arc == "sublayers" else ""
    body = f'def Xform "External" (prepend {arc} = @{reference}@) {{}}' if arc != "sublayers" else ""
    stage = package / "stage.usda"
    stage.write_text(
        f'#usda 1.0\n(\n metersPerUnit = 1\n upAxis = "Z"\n {declaration}\n)\n'
        f'def Xform "World" {{\n def Xform "Lab" {{\n {body}\n }}\n}}\n',
        encoding="utf-8",
    )
    opened, composed = [], []
    real_open = Sdf.Layer.FindOrOpen

    def observed_open(filename, *args, **kwargs):
        opened.append(filename)
        return real_open(filename, *args, **kwargs)

    def forbidden_composition(*args, **kwargs):
        composed.append(args)
        raise RuntimeError("Rejected package must not reach composition")

    monkeypatch.setattr(Sdf.Layer, "FindOrOpen", observed_open)
    monkeypatch.setattr(Usd.Stage, "Open", forbidden_composition)
    report = validate(
        {
            "kind": "scene",
            "subject": "scene:preflight-fixture@1",
            "metadata": {"stage": "stage.usda"},
            "types": {},
            "programs": {},
            "package": {"path": str(package), "files": {"stage.usda": str(stage), filename: str(target)}},
            "assets": [
                {
                    "id": "environment/archive-fixture@1",
                    "path": str(asset),
                    "contentHash": content_hash(asset),
                    "files": {filename: str(target)},
                }
            ]
            if reference_kind == "indexed"
            else [],
        }
    )
    failure = next(check for check in report["checks"] if check["id"] == "S7")
    assert failure["status"] == "fail" and "unsupported" in failure["message"].lower()
    assert composed == [] and opened == [str(stage)]


def test_shader_texture_asset_field_is_confined_without_treating_it_as_a_composition_layer(tmp_path):
    stage = tmp_path / "stage.usda"
    texture = tmp_path / "texture[1].png"
    texture.write_bytes(b"texture bytes are not USD composition layers")
    stage.write_text(
        """#usda 1.0
(metersPerUnit = 1
 upAxis = "Z")
def Xform "World" {
 def Xform "Lab" {}
 def Shader "Texture" {
  asset inputs:file = @texture[1].png@
 }
}
""",
        encoding="utf-8",
    )
    report = validate(
        {
            "kind": "scene",
            "subject": "scene:preflight-fixture@1",
            "metadata": {"stage": "stage.usda"},
            "types": {},
            "programs": {},
            "assets": [],
            "package": {
                "path": str(tmp_path),
                "files": {"stage.usda": str(stage), texture.name: str(texture)},
            },
        }
    )
    assert next(check for check in report["checks"] if check["id"] == "S7")["status"] == "pass", report
