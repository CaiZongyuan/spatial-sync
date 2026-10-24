"""One held-out authority for Agent cases and embodied episodes, including startup/recovery."""

from packages.agent_evaluation.scope import owns_session


def _records(manager):
    store = getattr(getattr(manager, "evaluations", None), "store", None)
    return store.records if store is not None else {}


def visible_session(manager, session):
    run = _records(manager).get(getattr(session, "evaluation_id", None), {})
    return run.get("split") != "test" or owns_session(session)


def visible_session_id(manager, identity):
    session = manager.sessions.get(identity)
    if session is not None:
        return visible_session(manager, session)
    return not private_recording(manager, identity)


def private_recording(manager, recording_id):
    records = _records(manager)
    session = manager.sessions.get(recording_id)
    starting = getattr(manager, "_starting_session", None)
    if session is None and starting is not None and starting.session_id == recording_id:
        session = starting
    if session is not None and records.get(session.evaluation_id, {}).get("split") == "test":
        return True
    return any(
        run.get("split") == "test"
        and any(
            attempt.get("recordingId") == recording_id
            for attempt in (*run.get("cases", []), *run.get("episodes", []))
        )
        for run in records.values()
    )
