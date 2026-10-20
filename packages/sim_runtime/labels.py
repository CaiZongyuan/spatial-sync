"""Stable identity from the pinned Newton model's labels, including Site shapes."""

from dataclasses import dataclass

from packages.protocol.math import normalize_entity_key


@dataclass(frozen=True)
class Identity:
    kind: str
    index: int
    entity_key: str
    stable: bool


@dataclass
class LabelIndex:
    bodies: list[Identity]
    shapes: list[Identity]
    joints: list[Identity]
    warnings: list[dict]


def resolve_labels(
    body_labels, shape_labels, joint_labels, *, site_indices=(), missing_labels=(), strict=False,
    shape_bodies=None, site_aliases=None,
) -> LabelIndex:
    warnings = []
    seen = {}

    def resolve(labels, kind):
        result = []
        for index, label in enumerate(labels):
            entity_kind = "site" if kind == "shape" and index in site_indices else kind
            # Newton fills absent labels before finalize. A caller that observed
            # an omission supplies provenance; nonempty labels remain authoritative.
            missing = not label or (kind, index) in missing_labels
            if missing and strict:
                raise ValueError(f"Missing label for {entity_kind}[{index}]")
            key = f"/__unlabeled/{entity_kind}_{index}" if missing else normalize_entity_key(label)
            identity = Identity(entity_kind, index, key, not missing)
            if key in seen:
                previous = seen[key]
                body_shape = (previous.kind == "body" and entity_kind == "shape"
                              and shape_bodies is not None and shape_bodies[index] == previous.index)
                site_visual = (previous.kind == "shape" and entity_kind == "site"
                               and (site_aliases or {}).get(index) == previous.index
                               and shape_bodies is not None
                               and shape_bodies[index] == shape_bodies[previous.index])
                if not (body_shape or site_visual):
                    raise ValueError(
                        f"Duplicate label {key}: {previous.kind}[{previous.index}] conflicts with "
                        f"{entity_kind}[{index}]"
                    )
            seen[key] = identity
            result.append(identity)
            if missing:
                warnings.append(
                    {
                        "code": "unlabeled_entity",
                        "entityKey": key,
                        "message": f"{entity_kind}[{index}] has no stable label; generated an index key",
                    }
                )
        return result

    bodies = resolve(body_labels, "body")
    shapes = resolve(shape_labels, "shape")
    joints = resolve(joint_labels, "joint")
    return LabelIndex(bodies, shapes, joints, warnings)


def labels_for_model(model, *, missing_labels=(), strict=False, site_aliases=None) -> LabelIndex:
    import newton

    flags = model.shape_flags.numpy()
    sites = {i for i, value in enumerate(flags) if value & int(newton.ShapeFlags.SITE)}
    transforms = model.shape_transform.numpy()
    colliding = int(newton.ShapeFlags.COLLIDE_SHAPES | newton.ShapeFlags.COLLIDE_PARTICLES)
    for site, original in (site_aliases or {}).items():
        if (site not in sites or not 0 <= original < len(flags) or original in sites
                or flags[original] & colliding
                or any(abs(float(a) - float(b)) > 1e-6
                       for a, b in zip(transforms[site], transforms[original], strict=True))):
            raise ValueError("Generated Config Site differs from its noncolliding USD visual")
    return resolve_labels(
        model.body_label,
        model.shape_label,
        model.joint_label,
        site_indices=sites,
        missing_labels=missing_labels,
        strict=strict,
        shape_bodies=model.shape_body.numpy(), site_aliases=site_aliases,
    )
