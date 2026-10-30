"""L0 motion inventory and ordered assembly ledger; no Session or live actions.

Inventory coverage is separate from collision verification. Pending or unsupported
geometry never produces a passing L0 result.
"""

import argparse
import json
import os
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import yaml

from packages.programs.cell_passage.workcell import base_action
from packages.programs.labware.assembly import attached_bodies
from packages.programs.labware.motion import LabwareMotionProgram
from packages.programs.robot_arm.newton import VELOCITY_LIMITS
from packages.programs.robot_arm.trajectory import plan_trajectory
from tools.cell_passage_plan import CENTRIFUGE, ROBOT, WORKCELL, compile_recipe, load_inputs


def interpolation(segment, elapsed):
    """The command interpolation used by the corresponding runtime program."""
    start, end = np.asarray(segment["start"]), np.asarray(segment["end"])
    duration = segment["duration"]
    u = min(max(elapsed / duration, 0), 1) if duration else 1
    if segment["interpolation"] == "arm-trapezoid":
        trajectory = plan_trajectory(
            start,
            end,
            VELOCITY_LIMITS,
            speed_scale=segment["speedScale"],
            speed=segment["speed"],
            acceleration=segment["acceleration"],
        )
        return trajectory.sample(elapsed)[0].tolist()
    if segment["interpolation"] == "flex-quintic":
        u = u**3 * (10 + u * (-15 + 6 * u))
    return (start + (end - start) * u).tolist()


class MotionLedger:
    def __init__(
        self, layout, locations, bindings, *, arm_initial=None, flex_initial=None, scalar_initial=None
    ):
        self.layout, self.locations = layout, locations
        self.bindings = {row["key"]: row for row in bindings["entities"]}
        self.placements = dict(layout["initialStations"])
        self.site_parents = {body + "/LidSeat": body for body in layout["lids"]}
        self.held = None
        self.tip = None
        self.arm = list(arm_initial) if arm_initial is not None else None
        self.flex = list(flex_initial) if flex_initial is not None else None
        self.scalars = dict(scalar_initial or {})
        self.rows = []
        self.gaps = []

    def assembly(self, body):
        if not body:
            return []
        pairs = [(self.site_parents.get(site, -1), child) for child, site in self.placements.items()]
        result = attached_bodies([body], pairs)
        return [body, *sorted(result - {body})]

    def _arm_plan(self, action, params):
        """Ask the actual Program to expand paths; admission is an L1 concern."""
        config = self.bindings[ROBOT]["config"]
        program = LabwareMotionProgram()
        program.locations = self.locations
        program.tubes = {key: i for i, key in enumerate(config["tubes"])}
        program.pick_tube = self.held
        program.gripper_open_width_m = config.get("automaticOpenWidthM", 0.08)
        program.symmetric_pre_insertion = set(config.get("symmetricPreInsertionLocations", []))
        program.accept = lambda inv, ctx: None
        physics = SimpleNamespace(body=np.zeros((len(program.tubes), 7)))

        def read(key, prop):
            if prop == "location":
                return self.placements.get(key)
            if prop == "lidClosed":
                lid = self.layout["lids"].get(key)
                return lid is not None and self.placements.get(lid) == key + "/LidSeat"
            return None

        context = SimpleNamespace(
            config=SimpleNamespace(**config),
            physics=physics,
            get=lambda key: self.held if key == "holding" else None,
            read=read,
            set=lambda **kwargs: None,
        )
        program.start(SimpleNamespace(action=action, params=params), context)
        return program.plan, program.pick_tube

    def _arm(self, action, params, row):
        plan, picked = self._arm_plan(action, params)
        if action in ("pick", "pickPlace"):
            row["pickCandidate"] = picked
        cfg = self.bindings[ROBOT]["config"]
        for kind, target, name in plan:
            if kind == "move":
                end = list(map(float, target))
                segment = {
                    "kind": "arm",
                    "start": self.arm,
                    "end": end,
                    "interpolation": "arm-trapezoid",
                    "location": name,
                    "speedScale": cfg.get("speedScale", 0.3),
                    "speed": params.get("speed", 1),
                    "acceleration": cfg.get("jointAccelerationRadPerS2", 2),
                    "assembly": self.assembly(self.held),
                }
                if self.arm is not None:
                    segment["duration"] = plan_trajectory(
                        self.arm,
                        end,
                        VELOCITY_LIMITS,
                        speed_scale=segment["speedScale"],
                        speed=segment["speed"],
                        acceleration=segment["acceleration"],
                    ).duration
                else:
                    segment["duration"] = None
                    row["gaps"].append("model_initial_arm_required")
                row["segments"].append(segment)
                self.arm = end
            elif kind == "pick":
                row["segments"].append({"kind": "gripper-close", "body": picked})
                source = self.placements.pop(picked)
                self.held = picked
                row["segments"].append(
                    {
                        "kind": "acquire",
                        "body": picked,
                        "sourceSite": source,
                        "assembly": self.assembly(picked),
                    }
                )
            elif kind == "place":
                if self.held is None:
                    raise ValueError("Placement without a held body")
                site = self.locations[name]["site"]
                row["segments"].append(
                    {"kind": "mate", "body": self.held, "site": site, "assembly": self.assembly(self.held)}
                )
                self.placements[self.held] = site
                self.held = None
            elif kind == "verifypick":
                row["segments"].append({"kind": "verify-lift", "minimumLiftM": 0.02, "body": picked})
            elif kind == "gripper":
                row["segments"].append(
                    {"kind": "gripper", "targetWidth": float(target), "assembly": self.assembly(self.held)}
                )

    def advance(self, step, *, stage, part):
        do = step.get("do")
        row = {
            "stage": stage,
            "part": part,
            "stepId": step["id"],
            "do": do,
            "motion": False,
            "segments": [],
            "gaps": [],
            "heldBefore": self.held,
            "assemblyBefore": self.assembly(self.held),
            "placementsBefore": dict(self.placements),
            "tipBefore": self.tip,
            "verification": "not-applicable",
        }
        if do:
            entity, action, params = do["entity"], do["action"], do.get("params", {})
            if entity == ROBOT and action in (
                "home",
                "moveJoints",
                "moveTo",
                "pick",
                "place",
                "pickPlace",
                "setGripper",
            ):
                row["motion"] = True
                try:
                    self._arm(action, params, row)
                except (KeyError, StopIteration, ValueError, AttributeError) as error:
                    row["gaps"].append("arm_expansion: " + str(error))
            elif entity == WORKCELL and action == "move":
                row["motion"] = True
                goal, cfg = list(params["position"]), self.bindings[WORKCELL]["config"]
                duration = None
                if self.flex is not None:
                    delta = np.abs(np.asarray(goal) - self.flex)
                    speed = np.array([cfg["xySpeedMPerS"]] * 2 + [cfg["zSpeedMPerS"]])
                    duration = max(
                        float(np.max(1.875 * delta / speed)),
                        float(np.sqrt(np.max((10 / np.sqrt(3)) * delta / cfg["axisAccelerationMPerS2"]))),
                    )
                else:
                    row["gaps"].append("model_initial_flex_required")
                row["segments"].append(
                    {
                        "kind": "flex",
                        "start": self.flex,
                        "end": goal,
                        "duration": duration,
                        "interpolation": "flex-quintic",
                        "tip": self.tip,
                    }
                )
                self.flex = goal
            elif (
                entity in self.bindings
                and self.bindings[entity]["program"] == "cell_passage.joint"
                and action in ("move", "open", "close")
            ):
                row["motion"] = True
                cfg = self.bindings[entity]["config"]
                goal = (
                    params["position"] if action == "move" else cfg["upper" if action == "open" else "lower"]
                )
                start = self.scalars.get(cfg["jointKey"])
                row["segments"].append(
                    {
                        "kind": "scalar",
                        "joint": cfg["jointKey"],
                        "start": start,
                        "end": goal,
                        "interpolation": "linear-command",
                        "duration": (abs(goal - start) / cfg["maxSpeed"] if start is not None else None),
                        "assembly": self.assembly(self.held),
                    }
                )
                self.scalars[cfg["jointKey"]] = goal
                if start is None:
                    row["gaps"].append("model_initial_scalar_required")
            elif entity == CENTRIFUGE and action in ("openLid", "closeLid", "start", "run"):
                row["motion"] = True
                cfg = self.bindings[entity]["config"]
                if action in ("openLid", "closeLid"):
                    joint = cfg["lidJoint"]
                    end = self.layout["centrifugeLidOpenRad"] if action == "openLid" else 0
                    row["segments"].append(
                        {
                            "kind": "joint-envelope",
                            "joint": joint,
                            "start": self.scalars.get(joint),
                            "end": end,
                            "coverageMethod": "full-bounded-joint-path",
                        }
                    )
                    self.scalars[joint] = end
                else:
                    row["segments"].append(
                        {
                            "kind": "rotor-cycle",
                            "joint": cfg["spindleJoint"],
                            "coverageMethod": "full-2pi-spatial-envelope",
                        }
                    )
            elif entity == WORKCELL and action == "pickupTip":
                row["motion"] = True
                self.tip = params["tip"]
                row["segments"].append({"kind": "tip-mate", "tip": self.tip})
            elif entity == WORKCELL and action == "discardTip":
                row["motion"] = True
                row["gaps"].append("discarded_tip_settling_state_required")
                row["segments"].append({"kind": "tip-release", "tip": self.tip})
                row["segments"].append({"kind": "pump-reset", "joint": self.layout["pumpJoint"], "end": 0})
                self.tip = None
            elif entity == WORKCELL and base_action(action) in ("aspirate", "dispense", "mix"):
                row["motion"] = True
                row["segments"].append(
                    {
                        "kind": "pump-envelope",
                        "joint": self.layout["pumpJoint"],
                        "start": 0,
                        "end": self.layout["pumpStrokeM"],
                        "coverageMethod": "full-allowed-stroke-conservative-envelope",
                    }
                )
        if row["motion"]:
            row["verification"] = "pending-geometry"
        row["heldAfter"] = self.held
        row["assemblyAfter"] = self.assembly(self.held)
        self.rows.append(row)
        return row


def inventory(library, *, initial=None):
    inputs = load_inputs(Path(library))
    scene = Path(library) / "scenes/cell-passage/1"
    bindings = yaml.safe_load((scene / "devices.yaml").read_text(encoding="utf-8"))
    locations = yaml.safe_load((scene / "locations.yaml").read_text(encoding="utf-8"))["locations"]
    recipe = compile_recipe(*inputs, owner="l0-offline", epoch=0, round_id="l0-sweep")
    ledger = MotionLedger(inputs[0], locations, bindings, **(initial or {}))
    for part in recipe["plans"]:
        for step in part["document"]["steps"]:
            ledger.advance(step, stage=part["stage"], part=part["part"])
    return ledger.rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--inventory-only", action="store_true")
    mode.add_argument("--check", action="store_true")
    parser.add_argument("--max-motion", type=int)
    parser.add_argument("--expected-contacts", type=Path)
    parser.add_argument("--qualified", action="store_true")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    geometry = None
    if args.check:
        from tools.cell_passage_sweep_geometry import GeometrySweep

        policy = json.loads(args.expected_contacts.read_text()) if args.expected_contacts else None
        overlay = None
        if isinstance(policy, dict) and all(isinstance(value, dict) for value in policy.values()):
            overlay = policy
            policy = {key: value.get("expectedContactPairs", []) for key, value in policy.items()}
        print(
            json.dumps(
                {
                    "event": "building-complete-model",
                    "pid": os.getpid(),
                    "expectedMemoryGB": "6-7",
                    "pruned": [],
                }
            ),
            flush=True,
        )
        geometry = GeometrySweep(
            args.library, expected_pairs=policy, config_overlay=overlay, qualified=args.qualified
        )
        (args.out / "geometry-signature.json").write_text(json.dumps(geometry.signature, indent=2))
        print(json.dumps({"event": "complete-model-ready", **geometry.signature}), flush=True)
    rows = inventory(args.library, initial=geometry.initial() if geometry else None)
    evaluated = 0
    if geometry:
        with (args.out / "sweep.jsonl").open("x", encoding="utf-8") as stream:
            for row in rows:
                if args.max_motion is not None and evaluated >= args.max_motion:
                    break
                result = geometry.run_row(row)
                row.update(result)
                if row["motion"]:
                    evaluated += 1
                    stream.write(json.dumps(row) + "\n")
                    stream.flush()
                    print(
                        json.dumps(
                            {
                                "event": "motion-checked",
                                "stepId": row["stepId"],
                                "samples": result["samples"],
                                "blockers": len(result["blockers"]),
                            }
                        ),
                        flush=True,
                    )
    (args.out / "steps.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    motions = [row for row in rows if row["motion"]]
    clear = sum(row["verification"] == "clear" for row in motions)
    summary = {
        "status": "inventory-only" if not geometry else "checked",
        "steps": len(rows),
        "motions": len(motions),
        "motionCounts": dict(Counter(row["do"]["action"] for row in motions)),
        "motionCountsByEntity": dict(
            Counter(row["do"]["entity"] + ":" + row["do"]["action"] for row in motions)
        ),
        "motionInventoryCoverage": 1.0,
        "evaluatedMotionCoverage": evaluated / len(motions),
        "verifiedMotionCoverage": clear / len(motions),
        "allPass": bool(geometry and geometry.declarations_available and clear == len(motions)),
        "gapCounts": dict(Counter(gap for row in rows for gap in row["gaps"])),
        "blocking": (["geometry backend not run"] if not geometry else [])
        + (
            ["shared expected-contact declarations unavailable"]
            if not geometry or not geometry.declarations_available
            else []
        ),
    }
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
