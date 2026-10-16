"""Run the complete I3 CPU laboratory journey through the public Source boundary."""

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apps.api.app.sources.newton_source import NewtonSource
from packages.programs.tube.facts import tube_location
from packages.protocol.math import compose_pose
from packages.protocol.schema import Command, InvokeRequest, SceneManifest, SessionOptions, TransformFrame

ROBOT, CENTRIFUGE = "/World/Lab/RobotA", "/World/Lab/Centrifuge"
TERMINAL = {"succeeded", "rejected", "failed", "cancelled"}


def validate_stats(receipt):
    stats = receipt["stats"]
    assert stats["tickTimingSamples"] > 0
    assert stats["tickP95Ms"] <= 2, stats
    assert stats["declaredWeldCount"] == 100 and stats["activeWeldCount"] == 0
    receipt["status"] = "passed"


async def journey(action, check_tube, duration):
    await action(CENTRIFUGE, "start", {"rpm": 3000, "durationSeconds": 20}, "precondition_failed")
    await action(ROBOT, "pickPlace", {"from": "rack.A1", "to": "centrifuge.slot1"})
    await check_tube(1, "/World/Lab/Centrifuge/Rotor/Slot1")
    await action(CENTRIFUGE, "closeLid", rejection="precondition_failed")
    await action(ROBOT, "home")
    await action(CENTRIFUGE, "closeLid")
    await action(CENTRIFUGE, "start", {"rpm": 3000, "durationSeconds": 20}, "imbalanced")
    await action(CENTRIFUGE, "openLid")
    await action(ROBOT, "pickPlace", {"from": "rack.A2", "to": "centrifuge.slot3"})
    await check_tube(2, "/World/Lab/Centrifuge/Rotor/Slot3")
    await action(ROBOT, "home")
    await action(CENTRIFUGE, "closeLid")
    await action(CENTRIFUGE, "start", {"rpm": 3000, "durationSeconds": duration})
    await action(CENTRIFUGE, "openLid")
    await check_tube(1, "/World/Lab/Centrifuge/Rotor/Slot1")
    await check_tube(2, "/World/Lab/Centrifuge/Rotor/Slot3")
    await action(ROBOT, "pickPlace", {"from": "centrifuge.slot1", "to": "rack.A1"})
    await check_tube(1, "/World/Lab/Rack/A1")
    await action(ROBOT, "pickPlace", {"from": "centrifuge.slot3", "to": "rack.A2"})
    await check_tube(2, "/World/Lab/Rack/A2")
    await action(ROBOT, "home")


def physical_check(manifest, previous, snapshot, values, index, expected):
    bodies = {node.entityKey: node.streamId for node in manifest.nodes if node.kind == "body"}
    poses = {row[0]: {"p": row[1:4], "q": row[4:]} for row in snapshot.poses}
    key = f"/World/Lab/Tubes/Tube{index:02d}"
    tube = poses[bodies[key]]
    before = next(row for row in previous.poses if row[0] == bodies[key])
    dt = snapshot.simTime - previous.simTime
    assert dt > 0, "Physical settling check needs two actual simulation samples"
    velocity = [(after - start) / dt for start, after in zip(before[1:4], tube["p"], strict=True)]
    site = next(site for site in manifest.sites if site.entityKey == expected)
    world = (
        compose_pose(poses[bodies[site.parentBodyKey]], site.localPose.model_dump())
        if site.parentBodyKey
        else site.localPose.model_dump()
    )
    assert values[key]["location"] == expected
    assert (
        tube_location([*tube["p"], *tube["q"]], velocity, [[*world["p"], *world["q"]]], [expected])
        == expected
    )
    return {"key": key, "expected": expected, "tube": tube, "site": world, "observedVelocity": velocity}


async def run(output, duration=20):
    source, receipt = NewtonSource("info-newton-smoke"), {"actions": [], "physicalChecks": []}
    request_index = 0
    try:
        manifest, catalog = await source.start("robot-and-tubes", SessionOptions(device="cpu"))
        assert len(catalog.entities) == 22
        await source.execute(Command(requestId="pause", command="pause"))
        await source.execute(Command(requestId="reset", command="reset"))

        def values():
            return {entity.key: entity.values for entity in source.entity_snapshot().entities}

        async def action(key, name, params=None, rejection=None):
            nonlocal request_index
            request_index += 1
            frame = source.latest_frame()
            request = InvokeRequest(
                invocationId=f"smoke-{request_index}",
                requestId=f"smoke-{request_index}",
                entityKey=key,
                action=name,
                params=params or {},
                epoch=frame.epoch,
                createdSimTime=frame.simTime,
                createdAtMs=time.monotonic() * 1000,
            )
            result = await source.invoke(request)
            deadline = time.monotonic() + 60
            while result.status not in TERMINAL:
                if time.monotonic() > deadline:
                    raise TimeoutError(f"{name} did not terminate")
                await source.execute(
                    Command(requestId=f"step-{request_index}", command="step", args={"count": 32})
                )
                await asyncio.sleep(0.002)
                if key == CENTRIFUGE and name == "start":
                    current = values()
                    samples = receipt.setdefault("runningSamples", [])
                    samples.append(
                        {
                            "simTime": source.latest_frame().simTime,
                            "centrifuge": current[CENTRIFUGE],
                            "tubeLocations": [
                                current[f"/World/Lab/Tubes/Tube{i:02d}"]["location"] for i in (1, 2)
                            ],
                        }
                    )
                result = next(
                    inv
                    for inv in source.entity_snapshot().invocations
                    if inv.invocationId == request.invocationId
                )
            record = result.model_dump(mode="json")
            receipt["actions"].append(record)
            output.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
            print(
                json.dumps({"action": name, "status": result.status, "error": record.get("error")}),
                flush=True,
            )
            if rejection:
                assert result.status == "rejected" and result.error.code == rejection, record
            else:
                if result.status != "succeeded":
                    receipt["failureState"] = source.entity_snapshot().model_dump(mode="json")
                    receipt["failureFrame"] = (await source.snapshot()).model_dump(mode="json")
                    receipt["failureStats"] = source.stats()
                assert result.status == "succeeded", record
            return result

        async def check_tube(index, expected):
            previous = await source.snapshot()
            await source.execute(Command(requestId=f"settle-{index}", command="step", args={"count": 24}))
            snapshot = await source.snapshot()
            receipt["physicalChecks"].append(
                physical_check(manifest, previous, snapshot, values(), index, expected)
            )

        await journey(action, check_tube, duration)
        receipt.update(
            stats=source.stats(),
            catalogMode=catalog.entities[ROBOT]["config"]["graspMode"],
            physicsImportedInCaller="newton" in sys.modules or "warp" in sys.modules,
        )
        validate_stats(receipt)
        assert not receipt["physicsImportedInCaller"]
    finally:
        await source.close()
        receipt["cleanup"] = source.stats()
        assert not receipt["cleanup"]["runnerAlive"] and not receipt["cleanup"]["readerThreadsAlive"]
        output.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    return receipt


async def run_cli(url, executable, output, duration=20):
    """Use the installed CLI for every device action, with independent HTTP poses."""
    receipt = {"entrypoint": str(executable), "actions": [], "physicalChecks": [], "runningSamples": []}
    route = None
    async with httpx.AsyncClient(base_url=url, timeout=180, trust_env=False) as client:
        try:
            response = await client.post(
                "/api/v1/sessions", json={"sceneId": "robot-and-tubes", "source": "newton"}
            )
            response.raise_for_status()
            session = response.json()
            route = "/api/v1/sessions/" + session["sessionId"]
            manifest = SceneManifest.model_validate((await client.get(route + "/scene")).json())
            catalog = (await client.get(route + "/catalog")).json()
            environment = os.environ | {"LW_URL": url, "LW_SESSION": session["sessionId"]}

            async def states():
                response = await client.get(route + "/state")
                response.raise_for_status()
                return {key: value["values"] for key, value in response.json()["entities"].items()}

            async def action(key, name, params=None, rejection=None):
                command = [
                    str(executable),
                    "--json",
                    "do",
                    key,
                    name,
                    *(f"{param}={json.dumps(value)}" for param, value in (params or {}).items()),
                    "--wait",
                    "--timeout",
                    "120",
                ]
                process = await asyncio.create_subprocess_exec(
                    *command, env=environment, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                )
                communication = asyncio.create_task(process.communicate())
                try:
                    while not communication.done():
                        try:
                            await asyncio.wait_for(asyncio.shield(communication), timeout=0.2)
                        except TimeoutError:
                            if key == CENTRIFUGE and name == "start":
                                current = await states()
                                receipt["runningSamples"].append(
                                    {
                                        "centrifuge": current[CENTRIFUGE],
                                        "tubeLocations": [
                                            current[f"/World/Lab/Tubes/Tube{i:02d}"]["location"]
                                            for i in (1, 2)
                                        ],
                                    }
                                )
                    stdout, stderr = await communication
                finally:
                    if process.returncode is None:
                        process.kill()
                        await process.wait()
                record = json.loads(stdout)
                receipt["actions"].append({"exitCode": process.returncode, **record})
                output.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
                print(
                    json.dumps(
                        {
                            "action": name,
                            "status": record.get("status"),
                            "exitCode": process.returncode,
                            "error": record.get("error"),
                        }
                    ),
                    flush=True,
                )
                if rejection:
                    assert process.returncode == 2 and record["error"]["code"] == rejection, record
                else:
                    if process.returncode:
                        receipt["failureState"] = await states()
                        receipt["stderr"] = stderr.decode()
                    assert process.returncode == 0 and record["status"] == "succeeded", record
                return record

            async def check_tube(index, expected):
                previous = TransformFrame.model_validate((await client.get(route + "/snapshot")).json())
                await asyncio.sleep(0.12)
                snapshot = TransformFrame.model_validate((await client.get(route + "/snapshot")).json())
                receipt["physicalChecks"].append(
                    physical_check(manifest, previous, snapshot, await states(), index, expected)
                )

            await journey(action, check_tube, duration)
            response = await client.get(route + "/snapshot")
            response.raise_for_status()  # Refresh source metrics at the final boundary.
            receipt.update(
                stats=(await client.get(route + "/stats")).json(),
                catalogMode=catalog["entities"][ROBOT]["config"]["graspMode"],
                runDurationSeconds=duration,
            )
            validate_stats(receipt)
        finally:
            if route:
                response = await client.delete(route)
                receipt["cleanup"] = {
                    "deleteStatus": response.status_code,
                    "health": (await client.get("/api/v1/health")).json(),
                }
            output.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / ".scratch/info-i3-newton/smoke.json")
    parser.add_argument("--duration", type=float, default=20)
    parser.add_argument("--url", default="http://127.0.0.1:8012")
    parser.add_argument(
        "--cli", type=Path, help="Installed lw executable; otherwise use the public Source seam"
    )
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    result = asyncio.run(
        run_cli(args.url, args.cli, args.output, args.duration)
        if args.cli
        else run(args.output, args.duration)
    )
    print(json.dumps({"status": result["status"], "stats": result["stats"]}, indent=2))
