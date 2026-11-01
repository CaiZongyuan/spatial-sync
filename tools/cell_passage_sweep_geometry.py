"""One complete-scene FK/contact model for L0; never launches a Session."""

import json
import math
from pathlib import Path

import numpy as np
import yaml

from packages.programs.labware.assembly import attached_bodies
from packages.programs.labware.contacts import classify_contact, validate_expected_contacts
from tools.cell_passage_plan import ROBOT, WORKCELL
from tools.cell_passage_sweep import interpolation


def compose(a, b):
    from scipy.spatial.transform import Rotation

    r = Rotation.from_quat(a[3:])
    return np.r_[a[:3] + r.apply(b[:3]), (r * Rotation.from_quat(b[3:])).as_quat()]


def relative(a, b):
    from scipy.spatial.transform import Rotation

    r = Rotation.from_quat(a[3:]).inv()
    return np.r_[r.apply(b[:3] - a[:3]), (r * Rotation.from_quat(b[3:])).as_quat()]


class GeometrySweep:
    def __init__(
        self,
        library,
        *,
        expected_pairs=None,
        config_overlay=None,
        qualified=False,
        control_hz=60,
        project_initial=True,
    ):
        import newton
        import warp as wp
        from newton.solvers import SolverMuJoCo
        from pxr import Usd, UsdGeom, UsdPhysics

        from factory.cell_passage_teach import composed_stage
        from packages.sim_runtime.loaders import import_usd
        from packages.sim_runtime.manifest_export import export_manifest
        from packages.sim_runtime.physics_port import PhysicsPort
        from packages.sim_runtime.scenes import SceneConfig
        from packages.sim_runtime.simulation import NewtonSimulation

        self.newton, self.wp = newton, wp
        self.sample_sink = None
        self.library = Path(library)
        self.stage = composed_stage(self.library)
        self.control_hz = control_hz
        scene = self.library / "scenes/cell-passage/1"
        self.layout = json.loads((scene / "mechanical-layout.json").read_text())
        self.bindings = {
            r["key"]: r for r in yaml.safe_load((scene / "devices.yaml").read_text())["entities"]
        }
        if qualified:
            from factory.cell_passage_qualified import qualified_placement_config

            config_overlay = qualified_placement_config(
                self.stage, self.layout, list(self.bindings.values()), enabled=True
            )
            expected_pairs = {key: delta["expectedContactPairs"] for key, delta in config_overlay.items()}
        for key, delta in (config_overlay or {}).items():
            self.bindings[key]["config"].update(delta)
        self.config_overlay = config_overlay or {}
        if expected_pairs is None and any(
            binding["config"].get("qualifiedPlacement", False) for binding in self.bindings.values()
        ):
            expected_pairs = {
                key: binding["config"].get("expectedContactPairs", [])
                for key, binding in self.bindings.items()
            }
        self.robot_cfg = self.bindings[ROBOT]["config"]
        self.flex_cfg = self.bindings[WORKCELL]["config"]
        self.declarations_available = expected_pairs is not None
        policies = (
            expected_pairs
            if isinstance(expected_pairs, dict)
            else {key: expected_pairs or [] for key in self.bindings}
        )
        self.expected = {key: validate_expected_contacts(value) for key, value in policies.items()}
        collision = {
            str(p.GetPath())
            for p in Usd.PrimRange(self.stage.GetPrimAtPath("/World/Lab"))
            if p.HasAPI(UsdPhysics.CollisionAPI)
            and UsdPhysics.CollisionAPI(p).GetCollisionEnabledAttr().Get() is not False
        }

        def builder():
            from copy import deepcopy

            from packages.library.templates import config_description
            from packages.sim_runtime.info_catalog import REGISTRY
            from packages.sim_runtime.library_scene import add_taught_sites, configured_weld_pairs
            from packages.sim_runtime.welds import declare_weld_pairs

            value = newton.ModelBuilder(up_axis=newton.Axis.Z)
            SolverMuJoCo.register_custom_attributes(value)
            paths = import_usd(value, self.stage, root_path="/World/Lab")
            effective = deepcopy(list(self.bindings.values()))
            for binding in effective:
                if binding["config"].get("locations") == "locations.yaml":
                    binding["config"]["locations"] = yaml.safe_load((scene / "locations.yaml").read_text())
            payload = {
                "structure": {"effectiveBindings": {"newton": effective}},
                "programs": {key: config_description(program) for key, program in REGISTRY.items()},
            }
            add_taught_sites(value, self.stage, paths, payload)
            bodies = {str(path): index for path, index in paths["path_body_map"].items()}
            sites = set(self.layout["stations"]) | set(self.layout["slotSites"])
            sites |= set(self.layout["initialStations"].values())
            owners = {}
            for site in sites:
                prim = self.stage.GetPrimAtPath(site).GetParent()
                while prim and str(prim.GetPath()) not in bodies:
                    prim = prim.GetParent()
                owners[site] = bodies[str(prim.GetPath())] if prim else -1
            pairs = configured_weld_pairs(list(self.bindings.values()), bodies, owners)
            if pairs:
                declare_weld_pairs(value, pairs)
            return value

        self.sim = NewtonSimulation(
            "l0-cell-passage", device="cpu", config=SceneConfig(builder, solver="mujoco")
        )
        self.model = self.sim.model
        manifest = export_manifest(
            self.sim, session_id="l0-offline", scene_revision="l0-offline", revision_numeric_id=1
        )
        self.port = PhysicsPort(self.sim, manifest)
        self.body = {key: i for i, key in enumerate(self.model.body_label)}
        self.joint = {key: i for i, key in enumerate(self.model.joint_label)}
        self.starts = self.model.joint_q_start.numpy()
        self.q = self.model.joint_q.numpy().copy()
        self.arm_indices = [self.starts[self.joint[k]] for k in self.robot_cfg["jointKeys"]]
        self.flex_indices = [self.starts[self.joint[k]] for k in self.layout["axisJoints"]]
        types, children = self.model.joint_type.numpy(), self.model.joint_child.numpy()
        self.free = {
            int(children[j]): int(self.starts[j])
            for j, kind in enumerate(types)
            if kind == newton.JointType.FREE
        }
        self.fingers = {int(children[self.joint[k]]) for k in self.robot_cfg["fingerKeys"]}
        self.finger_indices = [self.starts[self.joint[k]] for k in self.robot_cfg["fingerKeys"]]
        self.hand = self.body[self.robot_cfg["handBody"]]
        self.robot_bodies = set()
        robot_prim = self.stage.GetPrimAtPath("/World/Lab/Robot")
        for prim in Usd.PrimRange(robot_prim):
            if str(prim.GetPath()) in self.body:
                self.robot_bodies.add(self.body[str(prim.GetPath())])
        shapes = set(self.model.shape_label)
        missing = sorted(collision - shapes)
        if missing:
            raise ValueError("missingFixed/collision geometry: " + repr(missing[:20]))
        missing_objects = sorted(set(self.layout["graspOffsets"]) - set(self.body))
        missing_tips = sorted(t["bodyKey"] for t in self.layout["tips"] if t["bodyKey"] not in self.body)
        if missing_objects or missing_tips:
            raise ValueError("Missing live labware/tips: " + repr(missing_objects + missing_tips))
        self.signature = {
            "usdCollisionPrims": len(collision),
            "modelCollisionKeys": len(shapes),
            "missingFixed": [],
            "missingLiveObjects": [],
            "missingTips": [],
            "bodyCount": self.model.body_count,
            "shapeCount": self.model.shape_count,
            "tipCount": len(self.layout["tips"]),
            "pruned": [],
        }
        self.site_frames = {}
        cache = UsdGeom.XformCache()
        self.edges = {}
        self.current_sites = dict(self.layout["initialStations"])
        if project_initial:
            self._fk()
        else:
            self.base = self.sim.state.body_q.numpy().copy()
        for site in (
            set(self.current_sites.values())
            | set(self.layout["stations"])
            | set(self.layout["slotSites"])
            | {tip["site"] for tip in self.layout["tips"]}
            | {self.layout["tipSite"]}
        ):
            prim = self.stage.GetPrimAtPath(site)
            if not prim:
                raise ValueError("Missing declared Site " + site)
            matrix = cache.GetLocalToWorldTransform(prim)
            quat = matrix.ExtractRotationQuat()
            world = np.r_[list(matrix.ExtractTranslation()), *quat.GetImaginary(), quat.GetReal()]
            ancestor = prim.GetParent()
            while ancestor and str(ancestor.GetPath()) not in self.body:
                ancestor = ancestor.GetParent()
            parent = self.body[str(ancestor.GetPath())] if ancestor else None
            local = relative(self.base[parent], world) if parent is not None else world
            self.site_frames[site] = parent, local
        for child, site in self.current_sites.items():
            index = self.body[child]
            self.edges[index] = "site", site, relative(self.site_pose(site), self.base[index])
        self.held = None
        self.tip = None
        self.loose_disposals = []
        self.loose_disposal_ids = set()
        self.unresolved_passive = {}
        self.provisional = []
        self.width = float(sum(self.q[i] for i in self.finger_indices))
        if project_initial:
            self.sync()
        else:
            self.port.capture()
        self.signature["initialProjection"] = project_initial
        from tools.cell_passage_sweep_quantities import QuantityProjection

        self.quantity_sites = dict(self.current_sites)
        self.quantities = QuantityProjection(self.flex_cfg, self.quantity_read)

    def quantity_read(self, entity, prop):
        """Intent projection uses planned state; geometry eligibility is checked separately."""
        if prop == "station":
            return self.quantity_sites.get(entity)
        if prop == "settled":
            return entity in self.quantity_sites
        if prop == "lidClosed":
            return self.quantity_sites.get(self.layout["lids"].get(entity)) == entity + "/LidSeat"
        return None

    def projected_read(self, entity, prop):
        from packages.programs.labware.facts import seated_station

        if prop == "station":
            return self.current_sites.get(entity)
        if prop == "settled":
            site = self.current_sites.get(entity)
            return bool(
                site
                and seated_station(
                    self.pose(self.body[entity]), [np.zeros(3)], [self.site_pose(site)], [site]
                )
                == site
            )
        if prop == "lidClosed":
            lid = self.layout["lids"].get(entity)
            if not lid or self.held == lid:
                return False
            site = entity + "/LidSeat"
            return (
                seated_station(self.pose(self.body[lid]), [np.zeros(3)], [self.site_pose(site)], [site])
                == site
            )
        return None

    def _fk(self):
        self.sim.state.joint_q.assign(self.q)
        self.newton.eval_fk(self.model, self.sim.state.joint_q, self.sim.state.joint_qd, self.sim.state)
        self.base = self.sim.state.body_q.numpy()

    def pose(self, body, visiting=None):
        visiting = set() if visiting is None else visiting
        if body in visiting:
            raise ValueError("Attachment cycle")
        if body not in self.edges:
            return self.base[body].copy()
        visiting = visiting | {body}
        kind, parent, local = self.edges[body]
        anchor = self.site_pose(parent, visiting) if kind == "site" else self.pose(parent, visiting)
        return compose(anchor, local)

    def site_pose(self, site, visiting=None):
        parent, local = self.site_frames[site]
        return local.copy() if parent is None else compose(self.pose(parent, visiting), local)

    def active_pairs(self):
        result = []
        for child, (kind, parent, _) in self.edges.items():
            owner = self.site_frames[parent][0] if kind == "site" else parent
            result.append((-1 if owner is None else owner, child))
        return result

    def sync(self):
        self._fk()
        updates = {body: self.pose(body) for body in self.edges}
        for body, pose in updates.items():
            if body not in self.free:
                raise ValueError("Attached object lacks free joint: " + self.model.body_label[body])
            start = self.free[body]
            self.q[start : start + 7] = pose
        self._fk()
        self.port.capture()

    def initial(self):
        return {
            "arm_initial": self.q[self.arm_indices].tolist(),
            "flex_initial": self.q[self.flex_indices].tolist(),
            "scalar_initial": {key: float(self.q[self.starts[j]]) for key, j in self.joint.items()},
        }

    def contacts(self, scope, grasp=(), *, entity=ROBOT):
        import mujoco

        rows = self.port.collision_contacts(max_distance=0, bodies=set(scope))
        for data in (getattr(self.sim.solver, "mj_data", None), self.port._contact_data):
            if data is not None and hasattr(data, "warning"):
                for code in (mujoco.mjtWarning.mjWARN_CONTACTFULL, mujoco.mjtWarning.mjWARN_CNSTRFULL):
                    if data.warning[int(code)].number:
                        raise ValueError("Native contact/constraint arena is incomplete: " + str(code))
        expected, unexpected = [], []
        for contact in rows:
            if len(contact.shapes) != 2 or len(contact.bodies) != 2 or not math.isfinite(contact.distance_m):
                raise ValueError("Incomplete native contact facts")
            accepted = classify_contact(
                contact.shapes,
                self.expected.get(entity, ()),
                bodies=contact.bodies,
                finger_bodies=self.fingers,
                grasp_bodies=set(grasp),
            )
            value = {"shapes": list(contact.shapes), "depthM": -contact.distance_m}
            if self.loose_disposal_ids.intersection(contact.bodies):
                # Its last FK projection is not a known obstacle pose or a rest-state proof.
                # Retain the complete model and the unknown; join real L1 envelopes later.
                key = tuple(sorted(contact.shapes))
                self.unresolved_passive[key] = max(
                    self.unresolved_passive.get(key, 0), value["depthM"]
                )
                continue
            if accepted:
                expected.append(value)
            elif contact.distance_m < -0.002:
                unexpected.append(value)
        return expected, unexpected

    def capture_bounds(self, step_id, sample, scope):
        """Record actual sampled collider AABBs for later observed-tip envelope joins."""
        if self.sample_sink is None:
            return
        from scipy.spatial.transform import Rotation

        if not hasattr(self, "_sweep_bounds"):
            owners, flags = self.model.shape_body.numpy(), self.model.shape_flags.numpy()
            bodies = {
                int(owner)
                for owner, flag in zip(owners, flags, strict=True)
                if owner >= 0 and int(flag) & int(self.newton.ShapeFlags.COLLIDE_SHAPES)
            }
            self._sweep_bounds = {body: self.port.collision_bounds(body) for body in bodies}
        bodies = sorted(set(scope) & self._sweep_bounds.keys())
        if not bodies:
            return
        local = np.asarray([self._sweep_bounds[body] for body in bodies])
        corners = np.asarray([[x, y, z] for x in (0, 1) for y in (0, 1) for z in (0, 1)])
        points = local[:, :1, :] + (local[:, 1:2, :] - local[:, :1, :]) * corners[None, :, :]
        poses = self.port.frame_body[bodies]
        points = np.einsum("nij,nkj->nki", Rotation.from_quat(poses[:, 3:]).as_matrix(), points)
        points += poses[:, None, :3]
        bounds = np.stack([points.min(axis=1), points.max(axis=1)], axis=1)
        if not np.isfinite(bounds).all():
            raise ValueError("Nonfinite sampled collision bounds")
        self.sample_sink(step_id, sample, bodies, bounds)

    def gripper_path(self, target, *, body_key=None):
        from packages.programs.labware.grasp import physical_grasp

        speed = self.robot_cfg.get("gripperSpeedMPerS", 0.04)
        count = max(2, math.ceil(abs(target - self.width) / speed * self.control_hz) + 1)
        for width in np.linspace(self.width, target, count):
            self.width = float(width)
            self.q[self.finger_indices] = width / 2
            self.sync()
            qualified = False
            if body_key:
                body = self.body[body_key]
                hand = self.pose(self.hand)
                # Same physical TCP offset used by the pinned teaching/calibration.
                tcp = compose(hand, np.array([0.0, 0.0, 0.1034, 0.0, 0.0, 0.0, 1.0]))
                children = self.model.joint_child.numpy()
                pads = {
                    int(children[self.joint[joint]]): shape
                    for joint, shape in zip(
                        self.robot_cfg["fingerKeys"], self.robot_cfg["fingerPadShapes"], strict=True
                    )
                }
                qualified = physical_grasp(
                    self.pose(body),
                    tcp,
                    self.robot_cfg["graspOffsets"][body_key],
                    self.robot_cfg["gripDiameters"][body_key],
                    self.width,
                    self.port.collision_contacts(max_distance=0, bodies={body}),
                    body=body,
                    fingers=self.fingers,
                    pad_shapes=pads,
                    shape_prefix=self.robot_cfg["gripShapePrefixes"][body_key],
                    contact_allowance_m=0.002,
                )
            yield qualified
            if qualified:
                return

    def run_row(self, row):
        from packages.programs.labware.facts import seated_station

        blockers, expected_max, samples = [], {}, 0
        self.unresolved_passive = {}
        conditional = list(self.provisional)
        pickup_z = None
        moving = row["do"]["entity"] if row.get("do") else None
        self.quantity_sites = row["placementsBefore"]
        if moving == WORKCELL:
            self.quantities.observe(row["do"]["action"], row["do"].get("params", {}))
        scope = (
            set(self.robot_bodies)
            if moving != WORKCELL
            else {self.body[k] for k in self.layout["movingBodyKeys"] if k in self.body}
        )
        if moving in self.bindings and self.bindings[moving]["config"].get("movingBodyKeys"):
            scope = {self.body[k] for k in self.bindings[moving]["config"]["movingBodyKeys"]}
        if moving == WORKCELL:
            scope.update(self.body[k] for k in (self.flex_cfg["pipetteBody"],))
        for segment in row["segments"]:
            kind = segment["kind"]
            candidate = row.get("pickCandidate") or row.get("heldBefore") or self.held
            grasp = attached_bodies([self.body[candidate]], self.active_pairs()) if candidate else set()
            scope_now = scope | grasp
            if self.tip:
                scope_now |= attached_bodies([self.body[self.tip]], self.active_pairs())
            positions = []
            if kind in ("gripper", "gripper-close"):
                eligible = kind != "gripper-close"
                target = segment["targetWidth"] if kind == "gripper" else 0.0
                for qualified in self.gripper_path(target, body_key=segment.get("body")):
                    eligible |= qualified
                    expected, bad = self.contacts(scope_now, grasp, entity=moving)
                    samples += 1
                    self.capture_bounds(row["stepId"], samples, scope_now)
                    for value in expected:
                        key = tuple(sorted(value["shapes"]))
                        expected_max[key] = max(expected_max.get(key, 0), value["depthM"])
                    if bad:
                        blockers.append({"code": "unexpected_gripper_contact", "contacts": bad})
                if not eligible:
                    blockers.append({"code": "geometric_grasp_unqualified", "body": segment["body"]})
            if kind in ("arm", "flex", "scalar"):
                if segment["start"] is None:
                    blockers.append({"code": "unbound_initial_state", "segment": kind})
                    continue
                duration = segment["duration"]
                times = np.linspace(0, duration, max(2, math.ceil(duration * self.control_hz) + 1))
                positions = [interpolation(segment, t) for t in times]
            elif kind in ("joint-envelope", "rotor-cycle", "pump-envelope", "pump-reset"):
                joint = self.joint[segment["joint"]]
                scope_now |= attached_bodies(
                    [int(self.model.joint_child.numpy()[joint])], self.active_pairs()
                )
                start = float(self.q[self.starts[joint]])
                end = start + 2 * math.pi if kind == "rotor-cycle" else segment["end"]
                if kind == "pump-envelope":
                    do = row["do"]
                    lower = float(
                        self.model.joint_limit_lower.numpy()[self.model.joint_qd_start.numpy()[joint]]
                    )
                    upper = float(
                        self.model.joint_limit_upper.numpy()[self.model.joint_qd_start.numpy()[joint]]
                    )
                    plan, endpoints = self.quantities.stroke(
                        do["action"], do["params"], start, (lower, upper)
                    )
                    segment["runtimeQuantityPlan"] = plan
                    segment["coverageMethod"] = "runtime-resolved-command-travel"
                    position_parts = []
                    for a, b in zip(endpoints, endpoints[1:]):
                        count = max(2, math.ceil(abs(b - a) / 0.0005) + 1)
                        position_parts.extend(np.linspace(a, b, count).tolist())
                    positions = position_parts
                spacing = 0.0005 if kind in ("pump-envelope", "pump-reset") else 0.02
                if kind != "pump-envelope":
                    positions = np.linspace(start, end, max(2, math.ceil(abs(end - start) / spacing) + 1))
            for position in positions:
                if kind == "arm":
                    self.q[self.arm_indices] = position
                elif kind == "flex":
                    self.q[self.flex_indices] = position
                else:
                    self.q[self.starts[self.joint[segment["joint"]]]] = position
                self.sync()
                expected, bad = self.contacts(scope_now, grasp, entity=moving)
                samples += 1
                self.capture_bounds(row["stepId"], samples, scope_now)
                for value in expected:
                    key = tuple(sorted(value["shapes"]))
                    expected_max[key] = max(expected_max.get(key, 0), value["depthM"])
                if bad:
                    blockers.append({"code": "unexpected_contact", "sample": samples, "contacts": bad})
                    # Continue at the endpoint to expose later independent recipe gaps.
                    break
            if positions is not None and len(positions):
                position = positions[-1]
                if kind == "arm":
                    self.q[self.arm_indices] = position
                elif kind == "flex":
                    self.q[self.flex_indices] = position
                else:
                    self.q[self.starts[self.joint[segment["joint"]]]] = position
                self.sync()
            if kind == "acquire":
                body = self.body[segment["body"]]
                pickup_z = float(self.pose(body)[2])
                self.edges[body] = "body", self.hand, relative(self.pose(self.hand), self.pose(body))
                self.current_sites.pop(segment["body"], None)
                self.held = segment["body"]
            elif kind == "mate":
                body, site = self.body[segment["body"]], segment["site"]
                pose, target = self.pose(body), self.site_pose(site)
                occupied = [key for key, value in self.current_sites.items() if value == site]
                if occupied or seated_station(pose, [np.zeros(3)], [target], [site]) != site:
                    blockers.append(
                        {
                            "code": "mating_geometry",
                            "body": segment["body"],
                            "site": site,
                            "positionErrorM": float(np.linalg.norm(pose[:3] - target[:3])),
                            "occupied": occupied,
                        }
                    )
                self.edges[body] = "site", site, relative(target, pose)
                self.current_sites[segment["body"]] = site
                self.held = None
            elif kind == "verify-lift" and pickup_z is not None:
                lift = float(self.pose(self.body[segment["body"]])[2] - pickup_z)
                if lift < segment["minimumLiftM"]:
                    blockers.append({"code": "insufficient_lift", "actualLiftM": lift})
            elif kind == "tip-mate":
                from types import SimpleNamespace

                from packages.programs.cell_passage.tip_pickup import TipGeometry, check_pickup

                tip = next(t for t in self.layout["tips"] if t["id"] == segment["tip"])
                child, parent = self.body[tip["bodyKey"]], self.body[self.flex_cfg["pipetteBody"]]
                adapter = SimpleNamespace(
                    body_pose=lambda index: (self.pose(index)[:3], self.pose(index)[3:]),
                    site_pose=lambda name: (self.site_pose(name)[:3], self.site_pose(name)[3:]),
                    relative_site_velocity=lambda body, sites: np.zeros((len(sites), 3)),
                )
                problem = check_pickup(
                    adapter,
                    TipGeometry.model_validate(self.flex_cfg["tipGeometry"]),
                    child,
                    [tip["site"]],
                    self.layout["tipSite"],
                    self.flex_cfg["alignmentToleranceM"],
                )
                if problem:
                    blockers.append({"code": "tip_mating_geometry", "reason": problem.code})
                self.edges[child] = "body", parent, relative(self.pose(parent), self.pose(child))
                self.tip = tip["bodyKey"]
            elif kind == "tip-release" and self.tip:
                released = self.body[self.tip]
                self.edges.pop(released, None)
                self.loose_disposals.append(self.tip)
                self.loose_disposal_ids.add(released)
                self.tip = None
        if row["motion"] and not samples:
            self.sync()
            active = scope | (
                attached_bodies([self.body[self.tip]], self.active_pairs()) if self.tip else set()
            )
            _, bad = self.contacts(active, entity=moving)
            samples += 1
            self.capture_bounds(row["stepId"], samples, active)
            if bad:
                blockers.append({"code": "unexpected_contact", "contacts": bad})
        if self.loose_disposals:
            blockers.append({"code": "unsettled_disposals", "bodies": list(self.loose_disposals)})
        if blockers and not self.provisional:
            self.provisional.append(row["stepId"])
        return {
            "samples": samples,
            "blockers": blockers,
            "expectedContacts": [{"shapes": list(k), "maxDepthM": v} for k, v in expected_max.items()],
            "conditionalOnSteps": conditional,
            "dynamicUnknownBodies": list(self.loose_disposals),
            "unresolvedPassiveProjectionContacts": [
                {"shapes": list(key), "maxDepthM": value}
                for key, value in self.unresolved_passive.items()
            ],
            "verification": "blocked" if blockers else "conditional" if conditional else "clear",
        }
