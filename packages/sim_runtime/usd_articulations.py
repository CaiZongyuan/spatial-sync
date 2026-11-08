"""Complete explicit one-body USD roots during public builder construction."""
from contextlib import contextmanager


class RootImport:
    def __init__(self, builder, stage, root_path):
        import newton
        from pxr import Usd, UsdPhysics

        self.api = newton
        self.builder, self.stage = builder, stage
        self.first_joint = builder.joint_count
        prims = list(Usd.PrimRange(stage.GetPrimAtPath(root_path), Usd.TraverseInstanceProxies()))
        joints = [UsdPhysics.Joint(prim) for prim in prims if prim.IsA(UsdPhysics.Joint)]
        self.specs, self.recovered = {}, []
        for prim in prims:
            if not prim.HasAPI(UsdPhysics.RigidBodyAPI) or not prim.HasAPI(UsdPhysics.ArticulationRootAPI):
                continue
            connected = [joint for joint in joints if prim.GetPath() in
                         [*joint.GetBody0Rel().GetTargets(), *joint.GetBody1Rel().GetTargets()]]
            if len(connected) != 1:
                continue
            joint = connected[0]
            if (joint.GetBody0Rel().GetTargets() or joint.GetBody1Rel().GetTargets() != [prim.GetPath()]
                    or not joint.GetJointEnabledAttr().Get() or joint.GetExcludeFromArticulationAttr().Get()
                    or not (joint.GetPrim().IsA(UsdPhysics.PrismaticJoint)
                            or joint.GetPrim().IsA(UsdPhysics.RevoluteJoint))):
                continue
            self.specs[str(joint.GetPath())] = prim
        self.check_attributes()

    def check_attributes(self):
        if not self.specs:
            return []
        attributes = list(self.builder.custom_attributes.values())
        roots = list(self.specs.values())
        if any(attr.references == 'articulation' and (bool(attr.values) or any(
            attr.usd_attribute_name != '*' and prim.GetAttribute(attr.usd_attribute_name).HasAuthoredValue()
            for prim in roots
        )) for attr in attributes):
            raise ValueError('Root recovery cannot preserve authored articulation-index references')
        for attr in attributes:
            if attr.frequency != self.api.Model.AttributeFrequency.ARTICULATION:
                continue
            if attr.usd_value_transformer is not None and any(
                attr.usd_attribute_name == '*'
                or prim.GetAttribute(attr.usd_attribute_name).HasAuthoredValue()
                for prim in roots
            ):
                raise ValueError('Root recovery requires a supported declarative articulation attribute')
        return [attr for attr in attributes
                if attr.frequency == self.api.Model.AttributeFrequency.ARTICULATION]

    def drain(self, before=None):
        b, pending = self.builder, []
        for joint in range(self.first_joint, b.joint_count):
            prim = self.specs.get(str(b.joint_label[joint]))
            if prim is None or b.joint_articulation[joint] >= 0:
                continue
            child = b.joint_child[joint]
            connected = [j for j, (parent, body) in enumerate(zip(b.joint_parent, b.joint_child, strict=True))
                         if child in (parent, body)]
            if (connected != [joint] or b.joint_parent[joint] != -1
                    or str(b.body_label[child]) != str(prim.GetPath())
                    or b.joint_type[joint] not in (self.api.JointType.PRISMATIC, self.api.JointType.REVOLUTE)
                    or b.joint_world[joint] != b.current_world):
                raise ValueError('Explicit root does not identify one fresh scalar world joint')
            if before is None or joint < before:
                pending.append((joint, prim))
        last_end = max(b.articulation_end, default=0)
        if any(joint < last_end for joint, _ in pending):
            raise ValueError('Explicit root lies behind committed articulation boundary')
        attrs = self.check_attributes()
        prepared = [(joint, str(prim.GetPath()), self.api.usd.get_custom_attribute_values(prim, attrs))
                    for joint, prim in sorted(pending, key=lambda value: value[0])]
        for joint, label, values in prepared:
            self.original([joint], label=label, custom_attributes=values)
            self.recovered.append({'joint': joint, 'label': label, 'attributes': values,
                                    'world': b.current_world})

    def add(self, joints, label=None, custom_attributes=None):
        if custom_attributes and any(
            attr.references == 'articulation' and attr.key in custom_attributes
            for attr in self.builder.custom_attributes.values()
        ):
            raise ValueError('Root recovery cannot preserve authored articulation-index references')
        if isinstance(joints, list) and joints:
            self.drain(min(joints))
        return self.original(joints, label=label, custom_attributes=custom_attributes)


@contextmanager
def root_import(builder, stage, root_path):
    adapter = RootImport(builder, stage, root_path)
    adapter.original = builder.add_articulation
    if not adapter.specs:
        yield adapter
        return
    had_override = 'add_articulation' in builder.__dict__
    previous = builder.__dict__.get('add_articulation')
    builder.add_articulation = adapter.add
    try:
        yield adapter
        adapter.drain()
    finally:
        if had_override:
            builder.add_articulation = previous
        else:
            del builder.add_articulation
