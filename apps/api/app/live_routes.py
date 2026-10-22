"""Live status and coherent information bootstrap, independent of spatial sessions."""

from urllib.parse import urlsplit

from fastapi import HTTPException, Request

from apps.api.app.info_routes import install_info_routes


def install_live_routes(app, manager, settings, role_for):
    async def live_target():
        if manager.live is None:
            raise HTTPException(503, {"code": "live_unavailable", "message": "Live is not configured"})
        return manager.live

    install_info_routes(app, manager, role_for, prefix="/api/v1/live", resolve_target=live_target)

    @app.get("/api/v1/live")
    async def live_status(request: Request):
        live = await live_target()
        broker = urlsplit(live.bindings.broker.url)
        hostname = f"[{broker.hostname}]" if ":" in broker.hostname else broker.hostname
        port = broker.port or (8883 if broker.scheme == "mqtts" else 1883)
        return {"id": live.session_id, "name": live.bindings.live, "state": live.state,
                "error": live.error, "sceneRevision": live.catalog.sceneRevision,
                "epoch": live.epoch, "simTime": live.sim_time, "sampleTimeMs": live.sample_time_ms(),
                "eventSeq": live.info.event_seq, "role": role_for(request.headers),
                "broker": f"{broker.scheme}://{hostname}:{port}",
                "rules": live.catalog.alert_rules(),
                "recording": {"id": live.recording.metadata["sessionId"] if live.recording else None,
                              "status": live.recording.metadata["status"] if live.recording else "disabled",
                              "retentionDays": settings.live_retention_days},
                "layout": live.bindings.layout,
                "layoutContentHash": live.layout_version.content_hash if live.layout_version else None,
                "layoutError": live.layout_error,
                "placements": {entity.key: entity.placeAt for entity in live.bindings.entities}}

    @app.get("/api/v1/live/snapshot")
    async def live_snapshot():
        live = await live_target()
        return live.information_snapshot()
