"""Held-out Sessions have the same Target interface and no ordinary public projection."""

from packages.agent_evaluation.scope import owns_session


def visible_session(manager, session):
    records = getattr(getattr(manager, "evaluations", None), "store", None)
    run = records.records.get(getattr(session, "evaluation_id", None), {}) if records else {}
    return run.get("split") != "test" or owns_session(session)


def visible_session_id(manager, identity):
    session = manager.sessions.get(identity)
    if session is not None:
        return visible_session(manager, session)
    service = getattr(manager, "agent_rsi", None)
    return service is None or not service.private_recording(identity)
