"""Build, import, launch and run an owned Cell Passage demonstration candidate.

This external client never writes poses or material, and its result is not a
substitute for the required three Newton runs and independent acceptance.
"""

import argparse
import asyncio
import json
import math
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx
import yaml

from apps.cli.lw.client import GatewayClient, GatewayError
from packages.library.common import content_hash
from tools.cell_passage_audit import (
    audit_recording,
    audit_spatial_state,
    audit_tip_destinations,
    collect_cycle_evidence,
)
from tools.cell_passage_plan import compile_recipe, load_inputs
from tools.cell_passage_result import audit_material_result
from tools.cell_passage_session import execute_round, prepare_round

ROOT = Path(__file__).resolve().parents[1]


class _OwnedValidationClient(GatewayClient):
    """Match this owned gateway's physics wall budget, retaining ordinary RPC deadlines."""

    def __init__(self, *args, validation_budget=600, **kwargs):
        super().__init__(*args, **kwargs)
        self.validation_budget = validation_budget

    async def request(self, method, path, **kwargs):
        if method == "POST" and (path == "/api/v1/library/imports" or path.endswith("/validate")):
            kwargs["timeout"] = max(240, self.validation_budget + 30)
        return await super().request(method, path, **kwargs)


def gateway_app():
    """A normal gateway with owned roots and an instance identity for safe startup."""
    from apps.api.app.main import create_app
    from apps.api.app.settings import Settings

    root = Path(os.environ["SPATIAL_PASSAGE_RUN_ROOT"]).resolve() / "runtime"
    nonce = os.environ["SPATIAL_PASSAGE_OWNER_NONCE"]
    os.environ["LW_LIBRARY"] = str(root / "library")
    settings = Settings(
        state_root=root / "state", recordings_root=root / "recordings", scene_previews_root=root / "previews",
        allowed_origins=tuple(os.environ["SPATIAL_ALLOWED_ORIGINS"].split(",")), node_trainer=(),
        live_bindings=None, node_token=None, node_capabilities=None,
    )
    app = create_app(settings)

    @app.middleware("http")
    async def instance_identity(request, call_next):
        response = await call_next(request)
        response.headers["X-Passage-Owner"] = nonce
        return response

    return app


def _write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf8")


def _available(port):
    with socket.socket() as connection:
        # Match the server's reuse policy: a closed owned server may leave
        # TIME_WAIT connections, while a live listener must still reject bind.
        connection.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        connection.bind(("127.0.0.1", port))


def _asset_order(library):
    packages = {path.parent.relative_to(library / "assets").as_posix(): path.parent
                for path in sorted((library / "assets").rglob("asset.yaml"))}
    result, active, visited = [], set(), set()

    def visit(identity):
        if identity in visited:
            return
        if identity in active:
            raise ValueError("Generated asset dependency cycle: " + identity)
        active.add(identity)
        for path in packages[identity].rglob("*.usda"):
            for parts in re.findall(r"@library:assets/([^/@]+)/([^/@]+)/([1-9][0-9]*)/", path.read_text()):
                dependency = "/".join(parts)
                if dependency in packages and dependency != identity:
                    visit(dependency)
        active.remove(identity)
        visited.add(identity)
        result.append(packages[identity])

    for identity in packages:
        visit(identity)
    return result


async def _install(client, library, output, expected_upload_hash):
    """Observe/import exact dependencies, then retain and enforce scene admission."""
    receipts = []
    for package in _asset_order(library):
        metadata = yaml.safe_load((package / "asset.yaml").read_text(encoding="utf8"))
        expected = metadata["id"] + "@" + package.name
        upload_hash = content_hash(package)
        try:
            existing = await client.library_describe(expected, view="full")
        except GatewayError as error:
            if error.status_code != 404:
                raise
            existing = None
        report = (existing or {}).get("validation") or {}
        if (existing is not None and existing.get("assetVersion") == expected
                and report.get("importContentHash") == upload_hash):
            # A read observation is not a new import job. Published hashes also
            # include generated validation metadata, so compare the upload hash.
            receipt = {
                "kind": "asset", "assetVersion": expected,
                "contentHash": existing["contentHash"], "deduplicated": True,
                "observation": "existing_exact_upload_hash", "validation": report,
            }
        else:
            receipt = await client.library_import(package)
        receipts.append(receipt)
        _write(output / "imports.json", receipts)
        if receipt["assetVersion"] != expected:
            raise ValueError("Owned library assigned an unexpected dependency revision")
        if receipt["validation"].get("importContentHash") != upload_hash:
            raise ValueError("Imported dependency differs from the frozen source: " + expected)
        if any(check["status"] == "fail" for check in receipt["validation"]["checks"]):
            raise ValueError("Generated dependency failed validation: " + expected)
    scene = library / "scenes/cell-passage/1"
    if content_hash(scene) != expected_upload_hash:
        raise ValueError("Generated scene changed after its recipe inputs were frozen")
    receipt = await client.library_import(scene)
    if receipt["validation"].get("importContentHash") != expected_upload_hash:
        raise ValueError("Imported scene does not match the frozen recipe inputs")
    receipts.append(receipt)
    _write(output / "imports.json", receipts)
    report = receipt["validation"]
    if not (report.get("launchable") and report.get("physics", {}).get("status") == "completed"):
        report = await client.library_validate(receipt["sceneVersion"])
    _write(output / "validation.json", report)
    if not report.get("launchable") or any(
        check["status"] == "fail" for check in report["checks"]
    ):
        raise ValueError("Generated scene is not launchable; inspect validation.json")
    return receipt["sceneVersion"]


async def _claim(client, workcell, owner):
    session = await client.status()
    target = {name: session[name] for name in ("sessionId", "sceneRevision", "epoch")}
    config = (await client.catalog())["entities"][workcell]["config"]
    managed = config.get("managedDevices", [])
    initial = (await client.get_state([workcell]))["entities"][workcell]["values"]
    previous_owner, previous_epoch = initial["controlOwner"], initial["controlEpoch"]
    expected_epoch = previous_epoch + 1

    def witnessed(values, action, params):
        rows = values["materialSnapshot"]["controlRequests"]
        row = next((row for row in rows if row["operationId"] == params["operationId"]), None)
        if row is None:
            return None  # The source acknowledgement can precede fact publication.
        try:
            return (row["epoch"] == expected_epoch and row["action"] == action
                    and json.loads(row["paramsJson"]) == params)
        except (KeyError, TypeError, ValueError):
            return False

    async def same_session():
        current = await client.status()
        return all(current[name] == value for name, value in target.items())

    async def managed_idle():
        route = await client.route()
        for status in ("accepted", "running", "pending_approval"):
            rows = (await client.request("GET", route + "/invocations",
                                         params={"status": status, "limit": 1000}))["invocations"]
            if len(rows) >= 1000 or any(row["entityKey"] in managed for row in rows):
                return False
        return True

    async def observe(accepted, action, params, wanted, epochs, owners, modes):
        for _ in range(600):
            if not await same_session():
                return None
            invocation = await client.get_invocation(accepted["invocationId"])
            if (invocation["status"] not in ("accepted", "running", "succeeded")
                    or invocation["entityKey"] != workcell or invocation["action"] != action
                    or invocation["params"] != params):
                return None
            state = (await client.get_state([workcell, *managed]))["entities"]
            values = state[workcell]["values"]
            actual = (values["controlOwner"], values["controlEpoch"], values["controlMode"])
            if actual[0] not in owners or actual[1] not in epochs or actual[2] not in modes:
                return None
            receipt = witnessed(values, action, params)
            if receipt is False:
                return None
            started = invocation.get("startedSimTime")
            sampled = values.get("controlSafeAtSimTime")
            fresh = (type(sampled) in (int, float) and math.isfinite(sampled)
                     and started is not None and sampled >= started)
            if (actual == wanted and receipt is True and invocation["status"] == "succeeded"
                    and started is not None and fresh and values["controlSafe"] is True
                    and all(state[key]["values"].get("controlSafe") is True for key in managed)
                    and await managed_idle()):
                return values
            await asyncio.sleep(0.1)
        return None

    request = {"owner": previous_owner, "epoch": previous_epoch,
               "operationId": "claim-" + uuid.uuid4().hex, "nextOwner": owner, "mode": "auto"}
    if not await same_session():
        return None
    accepted = await client.invoke_action(workcell, "requestControl", request,
                                          wait=False, actor="cli:cell-passage")
    if accepted["status"] not in ("accepted", "running", "succeeded"):
        return None
    values = await observe(
        accepted, "requestControl", request, (previous_owner, expected_epoch, "holding"),
        {previous_epoch, expected_epoch}, {previous_owner}, {initial["controlMode"], "holding"},
    )
    if values is None:
        return None
    confirm = {"owner": previous_owner, "epoch": expected_epoch,
               "operationId": "confirm-" + uuid.uuid4().hex}
    if not await same_session():
        return None
    accepted = await client.invoke_action(workcell, "confirmControl", confirm,
                                          wait=False, actor="cli:cell-passage")
    if accepted["status"] not in ("accepted", "running", "succeeded"):
        return None
    values = await observe(
        accepted, "confirmControl", confirm, (owner, expected_epoch, "auto"),
        {expected_epoch}, {previous_owner, owner}, {"holding", "auto"},
    )
    return expected_epoch if values is not None else None


async def _start_session(client, identifier, output, *, startup_timeout_seconds=600, request_id=None):
    """Submit one known launch identity, then observe that request to readiness."""
    if (isinstance(startup_timeout_seconds, bool)
            or not math.isfinite(startup_timeout_seconds) or not 0 < startup_timeout_seconds <= 1800):
        raise ValueError("Startup timeout must be finite and in (0, 1800] seconds")
    request_id = request_id or "request-" + uuid.uuid4().hex
    if not re.fullmatch(r"request-[A-Za-z0-9_-]{1,72}", request_id):
        raise ValueError("A bounded request- launch identity is required")
    body = {"requestId": request_id, "sceneVersion": identifier, "source": "newton", "seed": 42,
            "continueWithoutObservers": True}
    record = {"status": "submitting", "request": body, "startupTimeoutSeconds": startup_timeout_seconds}
    started = time.monotonic()
    _write(output / "launch-request.json", record)
    try:
        async with asyncio.timeout(startup_timeout_seconds):
            try:
                value = await client.request("POST", "/api/v1/sessions", json=body,
                                             timeout=startup_timeout_seconds)
            except GatewayError as error:
                record["postError"] = error.document()
                _write(output / "launch-request.json", record)
                if error.status_code is not None:
                    raise
                value = {"requestId": request_id, "status": "unconfirmed"}
            while True:
                record.update(status="observing", response=value)
                record["elapsedWallSeconds"] = time.monotonic() - started
                _write(output / "launch-request.json", record)
                if value.get("requestId", request_id) != request_id:
                    raise ValueError("Gateway returned another launch request identity")
                if value.get("error") or value.get("status") in ("failed", "cancelled", "expired"):
                    raise ValueError("Owned launch did not complete; inspect launch-request.json")
                if value.get("sessionId"):
                    session = await client.request("GET", "/api/v1/sessions/" + value["sessionId"])
                    record["session"] = session
                    _write(output / "launch-request.json", record)
                    if session["state"] in ("RUNNING", "PAUSED"):
                        if session["sceneVersion"] != identifier:
                            raise ValueError("Owned launch returned another scene version")
                        record["status"] = "ready"
                        record["elapsedWallSeconds"] = time.monotonic() - started
                        _write(output / "launch-request.json", record)
                        return session
                    if session["state"] != "LOADING":
                        raise ValueError("Owned Source did not become ready")
                await asyncio.sleep(0.25)
                try:
                    value = await client.session_request_show(request_id)
                except GatewayError as error:
                    if error.status_code != 404:
                        raise
                    # A lost response is never permission to submit a new ID.
                    record["requestNotObserved"] = True
                    _write(output / "launch-request.json", record)
    except BaseException as error:
        record.update(status="interrupted", exceptionType=type(error).__name__)
        record["elapsedWallSeconds"] = time.monotonic() - started
        _write(output / "launch-request.json", record)
        raise


async def _prepare(args, inputs, bootstrap, identifier, round_id, output):
    url = f"http://127.0.0.1:{args.api_port}"
    bootstrap.pop("session", None)
    bootstrap.pop("launchCleanup", None)
    bootstrap.update(sessionRequestId="request-" + uuid.uuid4().hex, sessionClosed=False,
                     status="starting_source")
    _write(args.out / "bootstrap.json", bootstrap)
    async with GatewayClient(url=url) as client:
        session = await _start_session(client, identifier, output,
                                       startup_timeout_seconds=getattr(args, "startup_timeout_seconds", 600),
                                       request_id=bootstrap["sessionRequestId"])
    bootstrap.update(session=session, sceneVersion=identifier, status="launched", sessionClosed=False)
    _write(args.out / "bootstrap.json", bootstrap)
    if session["sceneVersion"] != identifier or session.get("sceneContentHash") is None:
        raise ValueError("Launched session lacks the exact immutable scene identity")
    if bootstrap.get("sceneContentHash") not in (None, session["sceneContentHash"]):
        raise ValueError("Repeated sessions do not share one immutable scene hash")
    bootstrap["sceneContentHash"] = session["sceneContentHash"]
    async with GatewayClient(url=url, session=session["sessionId"]) as client:
        owner = "runner-" + round_id
        epoch = await _claim(client, "/World/Lab/LiquidHandler", owner)
        if epoch is None:
            return {"status": "dispatch_stopped", "reason": "control_not_confirmed", "plans": []}
        recipe = compile_recipe(*inputs, owner=owner, epoch=epoch, round_id=round_id)
        if not recipe["environmentChecksAvailable"]:
            raise ValueError("The generated package lacks the required environment checks")
        _write(output / "recipe.json", recipe)
        record = await prepare_round(client, recipe, output / "round")
        if not args.prepare_only:
            record = await execute_round(client, record, output / "round")
            if record["status"] == "plans_completed":
                audit = await capture_final_state(client, inputs[0], recipe, record, output)
                if audit["status"] != "final_state_valid":
                    record["status"] = "final_state_invalid"
        return record


async def capture_final_state(client, layout, recipe, record, output):
    """Read public final quantities, actual poses, waste geometry and modeled environment."""
    route = await client.route()
    manifest = await client.request("GET", route + "/scene")
    before = await client.request("GET", route + "/snapshot")
    after = before
    for _ in range(300):
        if after["simTime"] - before["simTime"] >= 0.1:
            break
        await asyncio.sleep(0.05)
        after = await client.request("GET", route + "/snapshot")
    state = await client.get_state([])
    session = await client.status()
    _write(output / "final-observations.json", {
        "manifest": manifest, "before": before, "after": after, "state": state, "session": session,
    })
    values = {key: value["values"] for key, value in state["entities"].items()}
    workcell = values[record["workcell"]]
    mapping = [{"mother": f"Mother/{well}", "sample": f"Counting/{well}",
                "daughters": [f"DaughterA/{well}", f"DaughterB/{well}"]}
               for well in ("A1", "A2", "A3", "B1", "B2", "B3")]
    material = audit_material_result(record["initialMaterialSnapshot"], workcell["materialSnapshot"], mapping)
    goals = {"Mother": "WasteMother", "Counting": "WasteCounting", "DaughterA": "IncubatorA",
             "DaughterB": "IncubatorB", **{f"S{i}": f"RackS{i}" for i in range(1, 7)},
             "B5": "RackB5", "B6": "RackB6"}
    expected = {layout["bodies"][name]: "/World/Lab/Sites/" + site for name, site in goals.items()}
    expected.update({lid: body + "/LidSeat" for body, lid in layout["lids"].items()})
    spatial = audit_spatial_state(manifest, before, after, expected)
    tips = audit_tip_destinations(manifest, after, layout, workcell["materialSnapshot"])
    errors = []
    if any(session[name] != expected_value for name, expected_value in record["target"].items()):
        errors.append("Session/scene/epoch changed during final capture")
    if (workcell["controlOwner"] != record["controlOwner"]
            or workcell["controlEpoch"] != record["controlEpoch"] or workcell["controlMode"] != "auto"):
        errors.append("Control changed during final capture")
    if not spatial.get("nativeSource"):
        errors.append("Complete-run evidence requires an actual Newton Source")
    for name in goals:
        body = layout["bodies"][name]
        fact = values.get(body, {})
        if (fact.get("station") != expected[body] or fact.get("settled") is not True
                or fact.get("lidClosed") is not True):
            errors.append("Missing final actual station/lid/settling facts: " + name)
    incubator, cabinet = values.get(layout["incubatorEntity"], {}), values.get(layout["cabinetEntity"], {})
    if (incubator.get("environmentReady") is not True or incubator.get("doorKnown") is not True
            or incubator.get("doorOpen") is not False or cabinet.get("cabinetReady") is not True
            or incubator.get("signalSource") != "modeled" or cabinet.get("signalSource") != "modeled"):
        errors.append("Actual doors or modeled environmental readiness is not established")
    for key, (lower, upper) in layout["environmentLimits"].items():
        current = (cabinet if key in ("inflowMs", "sashHeightCm") else incubator).get(key)
        if not isinstance(current, (int, float)) or not lower <= current <= upper:
            errors.append("Environmental value outside the frozen teaching range: " + key)
    robot, centrifuge = values.get("/World/Lab/Robot/fr3_link0", {}), values.get("/World/Lab/Centrifuge", {})
    if robot.get("holding") is not None or robot.get("controlSafe") is not True:
        errors.append("Robot hold/safe state is not clear")
    if (centrifuge.get("phase") != "idle" or centrifuge.get("controlSafe") is not True
            or centrifuge.get("lidOpen") is not False or centrifuge.get("loadedEntities") != [None] * 4):
        errors.append("Centrifuge is not stopped, closed and empty")
    cycles = await collect_cycle_evidence(client, record, {
        stage: [layout["bodies"][name] for name in names]
        for stage, names in ((3, ("S1", "S2", "S3", "S4")), (4, ("S5", "S6", "B5", "B6")))
    })
    if cycles["status"] != "cycle_evidence_valid":
        errors.append("Actual completed centrifuge batch receipts are unavailable or inconsistent")
    if len(tips.get("discardedTips", [])) != recipe["tipsRequired"]:
        errors.append("Actual discarded tip inventory differs from this nominal recipe")
    valid = (not errors and material["status"] == "material_valid"
             and spatial["status"] == "spatial_state_valid" and tips["status"] == "tip_destinations_valid")
    report = {"status": "final_state_valid" if valid else "invalid", "errors": errors,
              "material": material, "spatial": spatial, "tipDestinations": tips, "cycles": cycles,
              "experimentQualified": False}
    _write(output / "final-audit.json", report)
    return report


async def _install_once(args, library, expected_upload_hash):
    budget = getattr(args, "startup_timeout_seconds", 600)
    async with _OwnedValidationClient(url=f"http://127.0.0.1:{args.api_port}",
                                     validation_budget=budget) as client:
        return await _install(client, library, args.out, expected_upload_hash)


def _close_owned_session(url, nonce, session_id):
    response = httpx.get(url + "/api/v1/health", timeout=2, trust_env=False)
    response.raise_for_status()
    if response.headers.get("X-Passage-Owner") != nonce:
        raise ValueError("Owned API identity changed; refusing session cleanup on a different server")
    httpx.delete(url + "/api/v1/sessions/" + session_id, timeout=30, trust_env=False).raise_for_status()


def _cancel_owned_launch(url, nonce, request_id):
    response = httpx.get(url + "/api/v1/health", timeout=2, trust_env=False)
    response.raise_for_status()
    if response.headers.get("X-Passage-Owner") != nonce:
        raise ValueError("Owned API identity changed; refusing launch cleanup")
    response = httpx.get(url + "/api/v1/session-requests/" + request_id, timeout=5, trust_env=False)
    if response.status_code == 404:
        return {"status": "not_observed"}
    response.raise_for_status()
    value = response.json()
    if value["status"] in ("queued", "starting", "cancelling"):
        response = httpx.delete(url + "/api/v1/session-requests/" + request_id, timeout=30, trust_env=False)
        response.raise_for_status()
        value = response.json()
    if value.get("sessionId"):
        _close_owned_session(url, nonce, value["sessionId"])
        value = value | {"sessionClosed": True}
    return value


def _audit_closed_recording(root, output, session_id, record):
    directory = (root / "runtime/recordings" / session_id).resolve()
    metadata = json.loads((directory / "recording.json").read_text(encoding="utf8"))
    files = {}
    for segment in metadata["segments"]:
        path = (directory / segment["file"]).resolve()
        if not path.is_relative_to(directory):
            raise ValueError("Recording segment escapes its owned directory")
        files[segment["file"]] = path.read_bytes()
    report = audit_recording(metadata, files, record["target"], record["completedParts"])
    _write(output / "recording-audit.json", report)
    return report


def demo(args):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", args.round):
        raise ValueError("A bounded round identity is required")
    if not 1 <= args.api_port <= 65535 or not 1 <= args.web_port <= 65535:
        raise ValueError("Ports must be in 1..65535")
    if not args.no_web and args.api_port == args.web_port:
        raise ValueError("API and viewer need different owned ports")
    startup_budget = getattr(args, "startup_timeout_seconds", 600)
    if (isinstance(startup_budget, bool) or not math.isfinite(startup_budget)
            or not 0 < startup_budget <= 1800):
        raise ValueError("Startup timeout must be finite and in (0, 1800] seconds")
    if not 1 <= args.repeats <= 20 or args.repeats > 1 and (len(args.round) > 60 or args.prepare_only):
        raise ValueError("Repeated automatic runs need 1..20 repeats and a <=60-character identity")
    _available(args.api_port)
    if not args.no_web:
        _available(args.web_port)
    args.out = args.out.resolve()
    args.out.mkdir(parents=True, exist_ok=False)
    library = args.generated.resolve() if args.generated else args.out / "generated"
    bootstrap = {"status": "building", "roundId": args.round, "experimentQualified": False}
    if getattr(args, "demonstration", False):
        bootstrap["demonstration"] = {"contactAllowanceM": .002, "formalPrecisionQualified": False}
    _write(args.out / "bootstrap.json", bootstrap)
    processes, logs = [], []
    nonce = uuid.uuid4().hex
    url = f"http://127.0.0.1:{args.api_port}"
    env = os.environ | {
        "SPATIAL_PASSAGE_RUN_ROOT": str(args.out), "SPATIAL_PASSAGE_OWNER_NONCE": nonce,
        "LW_LIBRARY": str(args.out / "runtime/library"),
        "SPATIAL_STATE_ROOT": str(args.out / "runtime/state"),
        "SPATIAL_RECORDINGS_ROOT": str(args.out / "runtime/recordings"),
        "SPATIAL_SCENE_PREVIEWS_ROOT": str(args.out / "runtime/previews"),
        "SPATIAL_ALLOWED_ORIGINS": f"http://127.0.0.1:{args.web_port}",
        "SPATIAL_API_URL": url, "SPATIAL_NEWTON_DEVICE": "cpu", "SPATIAL_RECORDINGS_ENABLED": "1",
        "SPATIAL_NODE_TRAINER": "[]",
        "SPATIAL_NEWTON_STARTUP_TIMEOUT_SECONDS": str(startup_budget),
        "SPATIAL_LIBRARY_PHYSICS_TIMEOUT_SECONDS": str(startup_budget),
    }
    if getattr(args, "demonstration", False) and env.get("SPATIAL_PASSAGE_CONTACT_TRACE_DIR"):
        env["SPATIAL_PASSAGE_CONTACT_TRACE_DIR"] = str(args.out / "raw-contacts")
        env["PYTHONPATH"] = os.pathsep.join((str(ROOT / "tools/passage_observer"),
                                           str(ROOT), str(ROOT / "apps/cli"), env.get("PYTHONPATH", "")))
    try:
        if args.source:
            for command in (
                [sys.executable, "-m", "factory.cell_passage", "--source", str(args.source.resolve()),
                 "--out", str(library), "--with-workcell"],
                [sys.executable, "-m", "factory.cell_passage_teach", "--library", str(library)],
            ):
                if getattr(args, "demonstration", False) and "factory.cell_passage" in command:
                    command.append("--demonstration")
                subprocess.run(command, cwd=ROOT, env=env, check=True)
        inputs = load_inputs(library)
        actual_demo = inputs[0].get("demonstration")
        if getattr(args, "demonstration", False) and not actual_demo:
            raise ValueError("Selected input was not generated with --demonstration")
        if actual_demo:
            bootstrap["demonstration"] = actual_demo
        scene_import_hash = content_hash(library / "scenes/cell-passage/1")
        _write(args.out / "source-inputs.json", {
            "layout": inputs[0], "teaching": inputs[1], "workcellBinding": inputs[2], "geometry": inputs[3],
            "sceneImportHash": scene_import_hash,
        })
        candidate = compile_recipe(*inputs, owner="auto", epoch=0, round_id=args.round)
        if not candidate["environmentChecksAvailable"]:
            raise ValueError("Generated scene is missing the environment/managed-device configuration")
        _available(args.api_port)
        commands = [([sys.executable, "-m", "uvicorn", "--factory", "tools.cell_passage:gateway_app",
                     "--host", "127.0.0.1", "--port", str(args.api_port)], "api")]
        if not args.no_web:
            _available(args.web_port)
            node = shutil.which("node")
            if node is None:
                raise ValueError("Node is required for the owned viewer; use --no-web for a CLI-only run")
            commands.append(([
                node, str(ROOT / "apps/web/node_modules/vite/bin/vite.js"),
                str(ROOT / "apps/web"), "--host", "127.0.0.1", "--port", str(args.web_port),
            ], "web"))
        for command, name in commands:
            log = (args.out / f"{name}.log").open("w", encoding="utf8")
            logs.append(log)
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            processes.append(subprocess.Popen(command, cwd=ROOT, env=env, stdout=log,
                                               stderr=subprocess.STDOUT, creationflags=flags))
        for _ in range(240):
            if any(process.poll() is not None for process in processes):
                raise ValueError("An owned service exited; inspect this run's logs")
            try:
                response = httpx.get(url + "/api/v1/health", timeout=1, trust_env=False)
                response.raise_for_status()
                if response.headers.get("X-Passage-Owner") == nonce:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.25)
        else:
            raise ValueError("Owned API readiness/identity could not be confirmed")
        bootstrap["status"] = "preparing"
        _write(args.out / "bootstrap.json", bootstrap)
        identifier = asyncio.run(_install_once(args, library, scene_import_hash))
        bootstrap["runs"] = []
        for index in range(1, args.repeats + 1):
            round_id = args.round if args.repeats == 1 else f"{args.round}-r{index}"
            output = args.out if args.repeats == 1 else args.out / "runs" / str(index)
            output.mkdir(parents=True, exist_ok=True)
            result = asyncio.run(_prepare(args, inputs, bootstrap, identifier, round_id, output))
            bootstrap["runs"].append({"roundId": round_id, "status": result["status"],
                                      "session": bootstrap["session"], "output": str(output)})
            _write(args.out / "bootstrap.json", bootstrap)
            if result["status"] != "plans_completed":
                break
            if index < args.repeats or args.exit_after_run:
                _close_owned_session(url, nonce, bootstrap["session"]["sessionId"])
                bootstrap["sessionClosed"] = True
                recording = _audit_closed_recording(
                    args.out, output, bootstrap["session"]["sessionId"], result
                )
                bootstrap["runs"][-1]["recording"] = recording
                if recording["status"] != "recording_valid":
                    result["status"] = "recording_invalid"
                    break
        bootstrap.update(status=result["status"], experimentQualified=False)
        _write(args.out / "bootstrap.json", bootstrap)
        print(json.dumps({"status": result["status"], "session": bootstrap["session"]["sessionId"],
                          "viewer": f"http://127.0.0.1:{args.web_port}/sessions/{bootstrap['session']['sessionId']}",
                          "evidence": str(args.out), "experimentQualified": False}), flush=True)
        if not args.exit_after_run:
            while all(process.poll() is None for process in processes):
                time.sleep(0.5)
        return 0 if result["status"] in ("plans_completed", "prepared") else 3
    except KeyboardInterrupt:
        if bootstrap["status"] == "plans_completed":
            bootstrap["viewerClosed"] = True
            return 0
        bootstrap["status"] = "interrupted"
        _write(args.out / "bootstrap.json", bootstrap)
        raise
    except BaseException:
        bootstrap["status"] = "interrupted"
        _write(args.out / "bootstrap.json", bootstrap)
        raise
    finally:
        if bootstrap.get("sessionRequestId") and not bootstrap.get("sessionClosed"):
            try:
                bootstrap["launchCleanup"] = _cancel_owned_launch(url, nonce, bootstrap["sessionRequestId"])
                if bootstrap["launchCleanup"].get("sessionClosed"):
                    bootstrap["sessionClosed"] = True
            except (httpx.HTTPError, ValueError):
                bootstrap["cleanup"] = "launch_cancel_unconfirmed"
        if bootstrap.get("session") and not bootstrap.get("sessionClosed"):
            try:
                _close_owned_session(url, nonce, bootstrap["session"]["sessionId"])
                bootstrap["sessionClosed"] = True
            except (httpx.HTTPError, ValueError):
                bootstrap["cleanup"] = "session_close_unconfirmed"
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        for log in logs:
            log.close()
        _write(args.out / "bootstrap.json", bootstrap)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--source", type=Path, help="Read-only assets02 platform_packages source")
    source.add_argument("--generated", type=Path, help="Already built/taught package root")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--round", default=uuid.uuid4().hex[:12])
    parser.add_argument("--repeats", type=int, default=1, help="Fresh sessions sharing one imported scene")
    parser.add_argument("--demonstration", action="store_true", help="Explicit 2 mm approximate one-run demo")
    parser.add_argument("--api-port", type=int, default=8023)
    parser.add_argument("--web-port", type=int, default=5223)
    parser.add_argument("--startup-timeout-seconds", type=float, default=600,
                        help="Owned Source/launch wall budget; device simulation deadlines stay fixed")
    parser.add_argument("--no-web", action="store_true")
    parser.add_argument("--prepare-only", action="store_true", help="Publish Plans for the browser client")
    parser.add_argument("--exit-after-run", action="store_true", help="Close owned services after the run")
    args = parser.parse_args()

    def interrupt(signum, frame):
        raise KeyboardInterrupt

    previous = signal.signal(signal.SIGTERM, interrupt)
    try:
        raise SystemExit(demo(args))
    finally:
        signal.signal(signal.SIGTERM, previous)


if __name__ == "__main__":
    main()
