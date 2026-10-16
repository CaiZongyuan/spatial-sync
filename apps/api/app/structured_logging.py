"""Gateway-owned JSON events; upstream Python/physics logger configuration is untouched."""

import json
import logging


def configure_gateway_logging(stream=None):
    logger = logging.getLogger("spatial.gateway")
    handler = next((item for item in logger.handlers if item.name == "spatial-json"), None)
    if handler is None:
        handler = logging.StreamHandler(stream)
        handler.name = "spatial-json"
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    elif stream is not None:
        handler.setStream(stream)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


def log_event(event, *, session_id, scene_revision, epoch, seq, level=logging.INFO, **fields):
    allowed = {"requestId", "command", "status", "state", "source", "code", "message", "cause", "runnerPID"}
    payload = {
        "event": event,
        "sessionId": session_id,
        "sceneRevision": scene_revision,
        "epoch": epoch,
        "seq": seq,
        **{key: value for key, value in fields.items() if key in allowed and value is not None},
    }
    logging.getLogger("spatial.gateway").log(
        level, json.dumps(payload, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    )
