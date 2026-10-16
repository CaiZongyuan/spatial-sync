"""Generate massless Newton Site shapes from the child's joint anchor transform."""

from packages.protocol.math import normalize_entity_key


def add_joint_anchor_sites(builder) -> dict[int, int]:
    import newton

    anchors = {}
    existing = {}
    for shape, label in enumerate(builder.shape_label):
        if label:
            existing.setdefault(normalize_entity_key(label), []).append(shape)
    for index, joint_type in enumerate(builder.joint_type):
        if joint_type == newton.JointType.FREE:
            continue
        label = f"{builder.joint_label[index]}/anchor"
        matches = existing.get(normalize_entity_key(label), [])
        if matches:
            if len(matches) != 1:
                raise ValueError(f"Joint anchor {label} has multiple matching shapes")
            shape = matches[0]
            if not builder.shape_flags[shape] & int(newton.ShapeFlags.SITE):
                raise ValueError(f"Joint anchor {label} is occupied by shape[{shape}], which is not a Site")
            if builder.shape_body[shape] != builder.joint_child[index]:
                raise ValueError(f"Joint anchor Site {label} must belong to the joint's child body")
            anchors[index] = shape
            continue
        anchors[index] = builder.add_site(
            builder.joint_child[index],
            xform=builder.joint_X_c[index],
            label=label,
            visible=True,
        )
        existing[normalize_entity_key(label)] = [anchors[index]]
    return anchors
