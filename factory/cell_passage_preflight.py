"""Whole-package admission and short real-CPU initial-state diagnostics.

This never starts a gateway or touches another Session. Its receipts distinguish
package failures, solver startup, initial seating and actual contact distances.
"""

import argparse
import json
import math
from pathlib import Path
from time import perf_counter

import numpy as np

from apps.api.app.library_validation import program_schemas
from packages.library.assets import AssetLibrary
from packages.library.common import TYPE_ROOT, document
from packages.library.scenes import SceneLibrary
from packages.library.structural import validate
from packages.library.validation import StructuralValidator
from packages.programs.labware.facts import seated_station
from packages.sim_runtime.library_scene import prepare_scene_config
from packages.sim_runtime.manifest_export import export_manifest
from packages.sim_runtime.physics_port import PhysicsPort
from packages.sim_runtime.simulation import NewtonSimulation


def payload(library, output):
    assets = AssetLibrary(output / "state", user_root=library)
    assets.startup()
    selected = SceneLibrary(user_root=library, state_root=output / "state").resolve("cell-passage@1")
    scope = StructuralValidator.scope
    value = {
        "kind": "scene",
        "subject": "scene:" + selected.id,
        "metadata": selected.metadata,
        "bindings": selected.bindings,
        "locations": selected.locations,
        "package": scope(selected.path),
        "assets": [
            scope(version.path, identifier=version.id, metadata=version.metadata, digest=version.content_hash)
            | {"root": version.root}
            for version in assets.versions.values()
        ],
        "programs": program_schemas(),
        "types": {value["type"]: value for file in TYPE_ROOT.glob("*.yaml") if (value := document(file))},
    }
    value["structure"] = validate(value)
    return value, selected


def run(library, output, seconds):
    library, output = Path(library).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError("A fresh receipt directory is required")
    output.mkdir(parents=True)
    started = perf_counter()
    data, selected = payload(library, output)
    (output / "structure.json").write_text(json.dumps(data["structure"], indent=2) + "\n")
    failures = [row for row in data["structure"]["checks"] if row["status"] == "fail"]
    if failures:
        raise ValueError("Structural admission failed: " + json.dumps(failures))
    print("structure passed", perf_counter() - started, flush=True)
    config = prepare_scene_config(data)
    with NewtonSimulation("cell-passage", device="cpu", config=config) as sim:
        initialized = perf_counter()
        print("solver initialized", initialized - started, flush=True)
        manifest = export_manifest(
            sim,
            session_id="passage-preflight",
            scene_revision="candidate",
            revision_numeric_id=1,
            strict_labels=True,
        )
        port = PhysicsPort(sim, manifest)
        layout = json.loads((selected.path / "mechanical-layout.json").read_text())
        sample_rows, timings, contacts, counts = [], [], [], []
        initial_mass = [
            {
                "entityKey": key,
                "massKg": float(sim.model.body_mass.numpy()[body]),
                "centerOfMassLocalM": sim.model.body_com.numpy()[body].tolist(),
            }
            for key, body in port.bodies.items()
            if "/Labware/" in key
        ]
        for frame in range(math.ceil(seconds * 60)):
            before = perf_counter()
            sim.step(4)
            timings.append((perf_counter() - before) * 1000)
            port.capture()
            counts.append(int(sim.solver.mj_data.ncon))
            if frame % 6 == 0 or frame == math.ceil(seconds * 60) - 1:
                observed = port.collision_contacts(max_distance=-0.001)
                contacts.extend(
                    {
                        "time": sim.sim_time,
                        "bodies": row.bodies,
                        "shapes": row.shapes,
                        "penetrationM": -row.distance_m,
                    }
                    for row in observed
                )
                row = {
                    "simTime": sim.sim_time,
                    "poses": {
                        key: port.body[body].tolist()
                        for key, body in port.bodies.items()
                        if "/Labware/" in key
                    },
                    "unseated": [],
                }
                for key, station in layout["initialStations"].items():
                    body = port.bind_body(key)
                    sites = port.bind_sites([station])
                    actual = seated_station(
                        port.body[body],
                        port.relative_site_velocity(body, sites),
                        port.site_poses(sites),
                        [station],
                    )
                    if actual != station:
                        row["unseated"].append(key)
                sample_rows.append(row)
                print("sample", frame, "contacts", counts[-1], "unseated", len(row["unseated"]), flush=True)
        receipt = {
            "schema": "lw.cell-passage-preflight/1",
            "scope": "Short whole-model initial state; no transport qualification",
            "sceneContentHash": selected.content_hash,
            "solverStartupSeconds": initialized - started,
            "wallSeconds": perf_counter() - started,
            "simulationSeconds": sim.sim_time,
            "counts": {
                "bodies": sim.model.body_count,
                "shapes": sim.model.shape_count,
                "joints": sim.model.joint_count,
            },
            "physicsControlStepMsP95": float(np.percentile(timings, 95)),
            "maxSolverContactCandidates": max(counts),
            "penetrationsOver1mm": len(contacts),
            "penetrationExamples": contacts[:50],
            "initialMass": initial_mass,
            "finalUnseated": sample_rows[-1]["unseated"],
            "finite": bool(np.isfinite(port.body).all()),
            "transportQualified": False,
        }
        (output / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
        (output / "samples.json").write_text(json.dumps(sample_rows) + "\n")
        (output / "contacts.json").write_text(json.dumps(contacts, indent=2) + "\n")
        print(json.dumps({key: value for key, value in receipt.items() if key != "initialMass"}, indent=2))
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--seconds", type=float, default=0.5)
    args = parser.parse_args()
    run(args.library, args.out, args.seconds)
