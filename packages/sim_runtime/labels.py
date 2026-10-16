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
    body_labels, shape_labels, joint_labels, *, site_indices=(), missing_labels=(), strict=False
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
                raise ValueError(
                    f"Duplicate label {key}: {previous.kind}[{previous.index}] conflicts with "
                    f"{entity_kind}[{index}]"
                )
            seen[key] = identity
            result.append(identity)
            if missing:
                warnings.append(
                    {
                        "code": "missing_label",
                        "entityKey": key,
                        "message": f"{entity_kind}[{index}] has no stable label; generated an index key",
                    }
                )
        return result

    bodies = resolve(body_labels, "body")
    shapes = resolve(shape_labels, "shape")
    joints = resolve(joint_labels, "joint")
    return LabelIndex(bodies, shapes, joints, warnings)


def labels_for_model(model, *, missing_labels=(), strict=False) -> LabelIndex:
    import newton

    sites = {i for i, flags in enumerate(model.shape_flags.numpy()) if flags & int(newton.ShapeFlags.SITE)}
    return resolve_labels(
        model.body_label,
        model.shape_label,
        model.joint_label,
        site_indices=sites,
        missing_labels=missing_labels,
        strict=strict,
    )
