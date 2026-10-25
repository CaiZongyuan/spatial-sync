"""Information targets include the independent Live Host, never extra spatial sessions."""


def information_targets(manager):
    from packages.info.evaluation_privacy import visible_session

    live = getattr(manager, "live", None)
    return (
        *(session for session in manager.sessions.values() if visible_session(manager, session)),
        *((live,) if live is not None else ()),
    )


def information_target(manager, identity):
    return next((target for target in information_targets(manager) if target.session_id == identity), None)


def alert_documents(target):
    project = getattr(target, "alert_documents", target.alerts.documents)
    return project()
