"""Generate massless Newton Site shapes from the child's joint anchor transform."""


def add_joint_anchor_sites(builder) -> dict[int, int]:
    import newton

    anchors = {}
    for index, joint_type in enumerate(builder.joint_type):
        if joint_type == newton.JointType.FREE:
            continue
        anchors[index] = builder.add_site(
            builder.joint_child[index],
            xform=builder.joint_X_c[index],
            label=f"{builder.joint_label[index]}/anchor",
            visible=True,
        )
    return anchors
