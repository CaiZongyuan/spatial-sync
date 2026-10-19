"""Import and validation contracts through uploaded bytes and real CPU USD inspection."""

import io
import struct
import tarfile
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import yaml
from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from packages.library.common import BUILTIN_ROOT


def scene_files(*, bad_posture=False):
    robot = "/World/Lab/RobotA"
    return {
        "scene.yaml": yaml.safe_dump(
            {
                "name": "structure-fixture",
                "title": "Structure fixture",
                "sources": ["newton"],
                "stage": "stage.usda",
            }
        ).encode(),
        "stage.usda": b"""#usda 1.0
(
    defaultPrim = "World"
    metersPerUnit = 1
    upAxis = "Z"
)
def Xform "World" {
    def Xform "Lab" {
        def Xform "RobotA" (
            prepend references = @library:assets/robot/fr3/1/asset.usdc@</Asset>
        ) {}
        def Xform "Target" {}
    }
}
""",
        "devices.yaml": yaml.safe_dump(
            {
                "scene": "structure-fixture",
                "entities": [
                    {
                        "key": robot,
                        "type": "robot-arm/v1",
                        "program": "robot_arm.newton",
                        "config": {
                            "jointKeys": [f"{robot}/fr3_joint{i}" for i in range(1, 8)],
                            "fingerKeys": [f"{robot}/fr3_finger_joint{i}" for i in (1, 2)],
                            "handBody": f"{robot}/fr3_hand",
                            "tcpSite": f"{robot}/fr3_hand/tcp",
                            "locations": "locations.yaml",
                            "tubes": [],
                            "graspMode": "weld",
                        },
                    }
                ],
            }
        ).encode(),
        "locations.yaml": yaml.safe_dump(
            {
                "robot": robot,
                "locations": {
                    "target": {
                        "site": "/World/Lab/Target",
                        "approach": [0, -0.4, 0, -2, 0, 1.6, 0.7],
                        "grasp": [0, 0] if bad_posture else [0, -0.4, 0, -2, 0, 1.6, 0.7],
                    }
                },
            }
        ).encode(),
    }


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


@pytest.fixture
def outside_http():
    accesses = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            accesses.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'#usda 1.0\ndef Xform "REMOTE_PRIVATE_CONTENT" {}\n')

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/outside.usda", accesses
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


def test_wrong_posture_reports_s8_before_unsupported_usd_runtime_and_allocates_no_session():
    with TestClient(create_app()) as client:
        imported = upload(client, scene_files(bad_posture=True))
        assert imported.status_code == 200, imported.text
        job = imported.json()
        assert job["sceneVersion"] == "structure-fixture@1"
        failed = [check for check in job["validation"]["checks"] if check["id"] == "S8"]
        assert any(check["status"] == "fail" and check["location"] == "target" for check in failed)
        assert (
            "weld"
            not in next(check for check in job["validation"]["checks"] if check["id"] == "S5")["message"]
        )
        launch = client.post(
            "/api/v1/sessions", json={"sceneVersion": job["sceneVersion"], "source": "newton"}
        )
        assert launch.status_code == 409, launch.text
        error = launch.json()["detail"]
        assert error["code"] == "scene_not_launchable"
        assert any(
            check["id"] == "S8" and check["status"] == "fail" for check in error["validation"]["checks"]
        )
        assert client.get("/api/v1/health").json()["sessions"] == []


@pytest.mark.parametrize("revision", [1, 2])
def test_unchanged_builtin_revalidation_remains_launchable(revision):
    with TestClient(create_app()) as client:
        response = client.post(f"/api/v1/library/scenes/lab-demo/{revision}/validate")
        assert response.status_code == 200, response.text
        report = response.json()
        assert next(c for c in report["checks"] if c["id"] == "S5")["status"] == "pass", report
        assert next(c for c in report["checks"] if c["id"] == "S8")["status"] == "pass", report
        launched = client.post(
            "/api/v1/sessions", json={"sceneVersion": f"lab-demo@{revision}", "source": "synthetic"}
        )
        assert launched.status_code == 201, launched.text


@pytest.mark.parametrize(
    "fault,expected",
    [
        ("unknown-site", "S5"),
        ("invalid-requires", "S5"),
        ("unknown-condition-entity", "S5"),
        ("unknown-condition-property", "S5"),
        ("wrong-condition-value", "S5"),
        ("invalid-interlock", "S5"),
        ("missing-poses", "S8"),
    ],
)
def test_declared_nested_location_and_interlock_faults_block_public_launch(fault, expected):
    files = scene_files()
    bindings = yaml.safe_load(files["devices.yaml"])
    location_document = yaml.safe_load(files["locations.yaml"])
    location = location_document["locations"]["target"]
    if fault == "unknown-site":
        location["site"] = "/World/Lab/Absent"
    elif fault == "missing-poses":
        del location["approach"], location["grasp"]
    elif fault == "invalid-interlock":
        bindings["entities"][0]["interlocks"] = {
            "home": [
                {
                    "entity": "/World/Lab/Missing",
                    "property": "bad",
                    "bogus": True,
                }
            ]
        }
    else:
        condition = {"property": "phase", "equals": "idle"}
        if fault == "invalid-requires":
            condition["bogus"] = True
        elif fault == "unknown-condition-entity":
            condition["entity"] = "/World/Lab/Absent"
        elif fault == "unknown-condition-property":
            condition["property"] = "not-a-property"
        elif fault == "wrong-condition-value":
            condition["equals"] = 123
        location["requires"] = [condition]
    bindings["entities"][0]["config"]["locations"] = location_document
    files["devices.yaml"] = yaml.safe_dump(bindings).encode()
    with TestClient(create_app()) as client:
        response = upload(client, files)
        assert response.status_code == 200, response.text
        report = response.json()["validation"]
        assert any(c["id"] == expected and c["status"] == "fail" for c in report["checks"]), report
        refused = client.post(
            "/api/v1/sessions", json={"sceneVersion": "structure-fixture@1", "source": "newton"}
        )
        assert refused.status_code == 409, refused.text
        assert refused.json()["detail"]["code"] == "scene_not_launchable"


def test_python_virtual_posture_count_is_checked_before_public_launch():
    path = BUILTIN_ROOT / "scenes/lab-demo/2"
    files = {name: (path / name).read_bytes() for name in ("scene.yaml", "devices.yaml", "locations.yaml")}
    metadata = yaml.safe_load(files["scene.yaml"])
    metadata["name"] = "python-structure-fixture"
    bindings = yaml.safe_load(files["devices.yaml"])
    bindings["scene"] = metadata["name"]
    for binding in bindings["entities"]:
        if "locations" in binding.get("config", {}):
            binding["config"]["locations"] = yaml.safe_load(files["locations.yaml"])
        if binding.get("program") == "robot_arm.virtual":
            binding["config"]["locations"]["locations"]["rack.A1"]["grasp"] = [0.0]
    files["scene.yaml"] = yaml.safe_dump(metadata).encode()
    files["devices.yaml"] = yaml.safe_dump(bindings).encode()
    with TestClient(create_app()) as client:
        response = upload(client, files)
        assert response.status_code == 200, response.text
        assert any(c["id"] == "S8" and c["status"] == "fail" for c in response.json()["validation"]["checks"])
        refused = client.post(
            "/api/v1/sessions", json={"sceneVersion": "python-structure-fixture@1", "source": "synthetic"}
        )
        assert refused.status_code == 409, refused.text


def test_native_python_builtin_revalidation_uses_pinned_urdf_joint_limits():
    with TestClient(create_app()) as client:
        response = client.post("/api/v1/library/scenes/robot-and-tubes/1/validate")
        assert response.status_code == 200, response.text
        checks = {item["id"]: item for item in response.json()["checks"]}
        assert checks["S5"]["status"] == checks["S8"]["status"] == "pass", checks
        assert response.json()["launchable"]


def test_native_python_taught_coordinate_outside_real_urdf_limit_blocks_public_launch():
    path = BUILTIN_ROOT / "scenes/robot-and-tubes/1"
    files = {name: (path / name).read_bytes() for name in ("scene.yaml", "devices.yaml", "locations.yaml")}
    metadata = yaml.safe_load(files["scene.yaml"])
    metadata["name"] = "native-python-structure-fixture"
    bindings = yaml.safe_load(files["devices.yaml"])
    bindings["scene"] = metadata["name"]
    locations = yaml.safe_load(files["locations.yaml"])
    locations["locations"]["rack.A1"]["grasp"][0] = 1000
    files["scene.yaml"] = yaml.safe_dump(metadata).encode()
    files["devices.yaml"] = yaml.safe_dump(bindings).encode()
    files["locations.yaml"] = yaml.safe_dump(locations).encode()
    with TestClient(create_app()) as client:
        response = upload(client, files)
        assert response.status_code == 200, response.text
        report = response.json()["validation"]
        assert any(c["id"] == "S8" and c["status"] == "fail" for c in report["checks"]), report
        refused = client.post(
            "/api/v1/sessions",
            json={
                "sceneVersion": "native-python-structure-fixture@1",
                "source": "newton",
            },
        )
        assert refused.status_code == 409, refused.text


def test_revalidation_persists_new_report_without_mutating_published_version(tmp_path):
    with TestClient(create_app()) as client:
        imported = upload(client, scene_files())
        assert imported.status_code == 200, imported.text
        package = tmp_path / "user-library/scenes/structure-fixture/1"
        original = {
            file.relative_to(package): file.read_bytes() for file in package.rglob("*") if file.is_file()
        }
        before = client.get("/api/v1/library/scenes/structure-fixture/1").json()
        response = client.post("/api/v1/library/scenes/structure-fixture/1/validate")
        assert response.status_code == 200, response.text
        report = response.json()
        assert report["schema"] == "lw.validation/1" and report["subject"] == "scene:structure-fixture@1"
        assert report["contentHash"] == before["contentHash"] and report["reportId"]
        assert {
            file.relative_to(package): file.read_bytes() for file in package.rglob("*") if file.is_file()
        } == original
        after = client.get("/api/v1/library/scenes/structure-fixture/1").json()
        assert after["contentHash"] == before["contentHash"]
        assert after["validation"]["reportId"] == report["reportId"]
    with TestClient(create_app()) as client:
        latest = client.get("/api/v1/library/scenes/structure-fixture/1").json()
        assert latest["validation"]["reportId"] == report["reportId"]


def test_scene_files_are_indexed_and_served_with_package_integrity(tmp_path):
    files = scene_files() | {"previews/structure.png": b"fixture preview bytes"}
    with TestClient(create_app()) as client:
        imported = upload(client, files)
        assert imported.status_code == 200, imported.text
        detail = client.get("/api/v1/library/scenes/structure-fixture/1").json()
        url = detail["files"]["previews/structure.png"]
        assert detail["thumbnailUrl"] == url
        response = client.get(url)
        assert response.status_code == 200 and response.content == files["previews/structure.png"]
        assert response.headers["content-type"] == "image/png"
        assert client.get(url.replace("structure.png", "unindexed.png")).status_code == 404
        (tmp_path / "user-library/scenes/structure-fixture/1/previews/structure.png").write_bytes(b"changed")
        changed = client.get(url)
        assert changed.status_code == 409 and changed.json()["detail"]["code"] == "scene_not_launchable"


@pytest.mark.parametrize("fault", ["invalid-config", "wrong-site-role", "missing-key", "duplicate-binding"])
def test_real_config_and_binding_faults_produce_s5_failure(fault):
    files = scene_files()
    bindings = yaml.safe_load(files["devices.yaml"])
    binding = bindings["entities"][0]
    if fault == "invalid-config":
        binding["config"]["speedScale"] = -1
    elif fault == "wrong-site-role":
        binding["config"]["tcpSite"] = "/World/Lab/RobotA/fr3_hand"
    elif fault == "missing-key":
        binding["key"] = "/World/Lab/Missing"
    else:
        bindings["entities"].append(binding.copy())
    files["devices.yaml"] = yaml.safe_dump(bindings).encode()
    with TestClient(create_app()) as client:
        response = upload(client, files)
        assert response.status_code == 200, response.text
        failure = next(check for check in response.json()["validation"]["checks"] if check["id"] == "S5")
        assert failure["status"] == "fail"


def test_s8_uses_robot_specific_confined_config_document():
    files = scene_files(bad_posture=True)
    bindings = yaml.safe_load(files["devices.yaml"])
    bindings["entities"][0]["config"]["locations"] = "taught/robot-a.yaml"
    files["devices.yaml"] = yaml.safe_dump(bindings).encode()
    files["taught/robot-a.yaml"] = scene_files()["locations.yaml"]
    with TestClient(create_app()) as client:
        response = upload(client, files)
        assert response.status_code == 200, response.text
        checks = {check["id"]: check for check in response.json()["validation"]["checks"]}
        assert checks["S5"]["status"] == checks["S8"]["status"] == "pass"


@pytest.mark.parametrize("location", ["../outside.yaml", "wrong-robot.yaml"])
def test_config_location_document_cannot_escape_package_or_select_another_robot(tmp_path, location):
    files = scene_files()
    (tmp_path / "outside.yaml").write_bytes(files["locations.yaml"])
    if location == "wrong-robot.yaml":
        files[location] = files["locations.yaml"].replace(b"/World/Lab/RobotA", b"/World/Lab/RobotB")
    bindings = yaml.safe_load(files["devices.yaml"])
    bindings["entities"][0]["config"]["locations"] = location
    files["devices.yaml"] = yaml.safe_dump(bindings).encode()
    with TestClient(create_app()) as client:
        response = upload(client, files)
        assert response.status_code == 200, response.text
        failure = next(check for check in response.json()["validation"]["checks"] if check["id"] == "S5")
        assert failure["status"] == "fail"


def test_incomplete_asset_is_inspectable_while_metadata_errors_allocate_no_revision():
    files = {
        "asset.yaml": (
            b"id: labware/incomplete-fixture\nkind: labware\ntitle: Incomplete\nstage: asset.usda\n"
        ),
        "asset.usda": b"""#usda 1.0
(defaultPrim = "Asset"
 metersPerUnit = 1
 upAxis = "Z")
def Xform "Asset" { def Cube "Shape" { double size = 0.02 } }
""",
    }
    with TestClient(create_app()) as client:
        malformed = upload(client, files | {"asset.yaml": files["asset.yaml"] + b"physics: {massKg: -1}\n"})
        assert malformed.status_code == 422 and malformed.json()["detail"]["problems"]
        imported = upload(client, files)
        assert imported.status_code == 200, imported.text
        assert imported.json()["assetVersion"] == "labware/incomplete-fixture@1"
        detail = client.get("/api/v1/library/assets/labware/incomplete-fixture/1").json()
        items = {item["id"]: item for item in detail["completeness"]["items"]}
        assert len(items) == 6
        assert items["license"]["status"] == items["collision"]["status"] == "missing"
        assert {"license", "collision"}.issubset(detail["gaps"])


@pytest.mark.parametrize("arc", ["references", "payload", "sublayers"])
@pytest.mark.parametrize("reference_kind", ["absolute", "escaping-relative", "remote"])
def test_unsafe_usd_dependencies_fail_s7_without_exposing_outside_content(
    tmp_path, request, arc, reference_kind
):
    outside = tmp_path / "outside.usda"
    secret = "REVIEWER_OWNED_OUTSIDE_SECRET_4719"
    outside.write_text(
        f'#usda 1.0\n(defaultPrim = "Outside")\ndef Xform "Outside" {{ def Xform "{secret}" {{}} }}\n',
        encoding="utf-8",
    )
    accesses = []
    if reference_kind == "remote":
        reference, accesses = request.getfixturevalue("outside_http")
    else:
        reference = outside.as_posix() if reference_kind == "absolute" else "../../../outside.usda"
    files = scene_files()
    text = files["stage.usda"].decode()
    if arc == "sublayers":
        text = text.replace('    upAxis = "Z"', f'    upAxis = "Z"\n    subLayers = [@{reference}@]')
    else:
        text = text.replace(
            '        def Xform "Target" {}',
            f'        def Xform "Target" {{}}\n'
            f'        def Xform "External" (prepend {arc} = @{reference}@) {{}}',
        )
    files["stage.usda"] = text.encode()
    with TestClient(create_app()) as client:
        response = upload(client, files)
        assert response.status_code == 200, response.text
        report = response.json()["validation"]
        failure = next(check for check in report["checks"] if check["id"] == "S7")
        assert failure["status"] == "fail"
        assert any(word in failure["message"] for word in ("host paths", "Absolute", "escapes"))
        assert secret not in response.text
        assert "REMOTE_PRIVATE_CONTENT" not in response.text and accesses == []
        launch = client.post(
            "/api/v1/sessions", json={"sceneVersion": "structure-fixture@1", "source": "newton"}
        )
        assert launch.status_code == 409
        assert any(
            check["id"] == "S7" and check["status"] == "fail"
            for check in launch.json()["detail"]["validation"]["checks"]
        )


def usdz_layer(contents):
    output = io.BytesIO()
    info = zipfile.ZipInfo("root.usda")
    padding = (64 - (30 + len(info.filename)) % 64) % 64
    if padding < 4:
        padding += 64
    info.extra = struct.pack("<HH", 0xFFFF, padding - 4) + bytes(padding - 4)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr(info, contents)
    return output.getvalue()


@pytest.mark.parametrize("arc", ["references", "payload", "sublayers"])
def test_nested_usdz_layers_cannot_bypass_dependency_preflight(tmp_path, arc):
    outside = tmp_path / "outside.usda"
    secret = "私有SECRET_ARCHIVE_BOUNDARY_12"
    outside.write_text(
        f'#usda 1.0\n(defaultPrim = "Outside")\ndef Xform "Outside" {{\n def Xform "{secret}" {{}}\n}}\n',
        encoding="utf-8",
    )
    nested = (
        f'#usda 1.0\n(defaultPrim = "External")\ndef Xform "External" '
        f"(prepend references = @{outside.as_posix()}@</Outside>) {{}}\n"
    )
    files = scene_files()
    files["bundle.usdz"] = usdz_layer(nested)
    text = files["stage.usda"].decode()
    if arc == "sublayers":
        text = text.replace('    upAxis = "Z"', '    upAxis = "Z"\n    subLayers = [@bundle.usdz@]')
    else:
        text = text.replace(
            '        def Xform "Target" {}',
            f'        def Xform "Target" {{}}\n'
            f'        def Xform "Archive" (prepend {arc} = @bundle.usdz@) {{}}',
        )
    files["stage.usda"] = text.encode()
    with TestClient(create_app()) as client:
        response = upload(client, files)
        assert response.status_code == 200, response.text
        failure = next(check for check in response.json()["validation"]["checks"] if check["id"] == "S7")
        assert failure["status"] == "fail" and "unsupported" in failure["message"].lower()
        assert "SECRET_ARCHIVE_BOUNDARY_12" not in response.text


@pytest.mark.parametrize("invalid", ["missing-joint", "blocked-limit"])
def test_invalid_joint_configuration_is_a_report_failure_not_a_worker_crash(invalid):
    files = scene_files()
    if invalid == "missing-joint":
        bindings = yaml.safe_load(files["devices.yaml"])
        bindings["entities"][0]["config"]["jointKeys"][0] = "/World/Lab/RobotA/missing"
        files["devices.yaml"] = yaml.safe_dump(bindings).encode()
        expected = "S5"
    else:
        files["stage.usda"] = files["stage.usda"].replace(
            b"        ) {}",
            b'        ) {\n            over "fr3_joint1" {\n'
            b"                float physics:lowerLimit = None\n            }\n        }",
        )
        expected = "S8"
    with TestClient(create_app()) as client:
        response = upload(client, files)
        assert response.status_code == 200, response.text
        report = response.json()["validation"]
        assert any(check["id"] == expected and check["status"] == "fail" for check in report["checks"])
        launch = client.post(
            "/api/v1/sessions", json={"sceneVersion": "structure-fixture@1", "source": "newton"}
        )
        assert launch.status_code == 409 and launch.json()["detail"]["code"] == "scene_not_launchable"
