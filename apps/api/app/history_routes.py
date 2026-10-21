"""Read-only history over an active recording checkpoint or a retained recording."""

import asyncio
import json
from contextlib import nullcontext
from typing import Literal

from fastapi import HTTPException, Query, Response

from packages.info.history import LIMIT, aggregate, query_window, read_samples
from packages.info.recording import recording_metadata


def install_history_routes(app, manager, repository):
    @app.get("/api/v1/history")
    async def history(
        response: Response,
        key: str = Query(min_length=1),
        property: str = Query(min_length=1),
        since: str = Query(min_length=1),
        until: str | None = None,
        every: str | None = None,
        agg: Literal["mean", "last", "min", "max"] = "mean",
        recording: str | None = None,
        sessionId: str | None = None,
    ):
        if recording is not None and sessionId is not None:
            raise HTTPException(422, {"code": "invalid_history_query",
                                      "message": "Choose recording or sessionId"})
        live_query = False
        if recording is None:
            if key.startswith("/Real/"):
                session = getattr(manager, "live", None)
                if session is None or session.state not in ("RUNNING", "PAUSED"):
                    raise HTTPException(503, {"code": "live_unavailable",
                                              "message": "Live Lab is not available"})
                if sessionId is not None and sessionId != session.session_id:
                    raise HTTPException(422, {"code": "invalid_history_query",
                                              "message": "The selected session is not this Live Lab"})
                live_query = True
            else:
                sessions = [manager.get(sessionId)] if sessionId else manager.sessions.values()
                session = next((item for item in sessions if item.state in ("RUNNING", "PAUSED")), None)
            if session is None:
                raise HTTPException(409, {"code": "history_unavailable",
                                          "message": "No active session recording"})
            if session.recording is None:
                raise HTTPException(409, {"code": "history_unavailable", "message": "Recording is disabled"})
            recording = session.recording.metadata["sessionId"] if live_query else session.session_id
        else:
            session = manager.sessions.get(recording)
            live = getattr(manager, "live", None)
            if (live is not None and live.recording is not None
                    and live.recording.metadata["sessionId"] == recording):
                session = live
        log = session.recording if session is not None else None
        # A Live reader pins before its checkpoint, through actual threaded I/O.
        with log.reader() if hasattr(log, "reader") else nullcontext():
            try:
                if log is not None:
                    metadata = await log.checkpoint()
                    directory = log.directory
                else:
                    path = await asyncio.to_thread(repository.resolve, recording, "recording.json")
                    directory = path.parent
                    metadata = recording_metadata(json.loads(await asyncio.to_thread(path.read_bytes)))
                if metadata.get("sessionId") != recording:
                    raise ValueError("Recording identity does not match")
                if not metadata["segments"]:
                    raise HTTPException(409, {"code": "history_unavailable",
                                              "message": "Recording has no samples"})
                end = (session.sample_time_ms() if live_query
                       else max(segment["endSampleTimeMs"] for segment in metadata["segments"]))
            except (OSError, ValueError, KeyError, TypeError) as error:
                raise HTTPException(503, {"code": "history_unavailable",
                                          "message": "Recording is unavailable"}) from error
            try:
                lower, upper, interval = query_window(since, until, every, end)
            except ValueError as error:
                raise HTTPException(422, {"code": "invalid_history_query", "message": str(error)}) from error
            try:
                read = log.read_pinned if hasattr(log, "read_pinned") else asyncio.to_thread
                samples = await read(read_samples, directory, metadata, key, property, lower, upper)
            except (OSError, ValueError, KeyError, TypeError) as error:
                raise HTTPException(503, {"code": "history_unavailable",
                                          "message": "Recording cannot be read"}) from error
        try:
            points, truncated = await asyncio.to_thread(aggregate, samples, lower, upper, interval, agg)
        except ValueError as error:
            raise HTTPException(422, {"code": "invalid_history_query", "message": str(error)}) from error
        response.headers.update({
            "X-History-Recording": recording,
            "X-History-Limit": str(LIMIT),
            "X-History-Truncated": str(truncated).lower(),
            "X-History-Since-Ms": str(lower),
            "X-History-Until-Ms": str(upper),
            "X-History-Every-Ms": "raw" if interval is None else str(interval),
            "X-History-Agg": agg,
        })
        if truncated:
            response.headers["X-History-Explanation"] = (
                "Returned the first 2000 points; narrow the window or increase every."
            )
        return points
