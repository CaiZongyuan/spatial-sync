"""Map the Live public timestamp boundary without mutating raw device state."""


def public_message(message, map_time):
    """Only protocol envelope/property sample clocks share this mapping.

    Invocation creation times are UTC. Event data is application-owned. Neither
    is rewritten, and a property's original measurement time stays independent
    of a snapshot/observation's envelope time.
    """
    updates = {}
    if hasattr(message, "sampleTimeMs"):
        updates["sampleTimeMs"] = map_time(message.sampleTimeMs)
    for field in ("entities", "changes"):
        if hasattr(message, field):
            updates[field] = [entity.model_copy(update={
                "meta": {prop: meta.model_copy(update={"sampleTimeMs": map_time(meta.sampleTimeMs)})
                         for prop, meta in entity.meta.items()},
            }) for entity in getattr(message, field)]
    return message.model_copy(update=updates) if updates else message
