import json
import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    state_root: Path = field(
        default_factory=lambda: Path(
            os.getenv("SPATIAL_STATE_ROOT", str(Path.home() / ".spatial-sync" / "state"))
        )
    )
    recordings_root: Path = field(
        default_factory=lambda: Path(os.getenv("SPATIAL_RECORDINGS_ROOT", "recordings"))
    )
    recordings_keep: int = field(default_factory=lambda: int(os.getenv("SPATIAL_RECORDINGS_KEEP", "20")))
    recordings_enabled: bool = field(
        default_factory=lambda: os.getenv("SPATIAL_RECORDINGS_ENABLED", "1") != "0"
    )
    live_retention_days: int = field(
        default_factory=lambda: int(os.getenv("SPATIAL_LIVE_RETENTION_DAYS", "7"))
    )
    approval_timeout_seconds: float = 600
    heartbeat_interval_seconds: float = 1.0
    send_timeout_seconds: float = 2.0
    reliable_queue_size: int = 128
    remote: bool = False
    allowed_origins: tuple[str, ...] = (
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:5174",
        "http://localhost:5174",
        "http://127.0.0.1:5184",
        "http://localhost:5184",
    )
    operator_token: str | None = None
    viewer_token: str | None = None
    remote_requests_per_minute: int = 120
    node_token: str | None = field(default_factory=lambda: os.getenv("SPATIAL_NODE_TOKEN"))
    node_trainer: tuple[str, ...] = field(default_factory=lambda: tuple(json.loads(
        os.getenv("SPATIAL_NODE_TRAINER", "[]"))))
    node_capabilities: Path | None = field(default_factory=lambda: (
        Path(os.environ["SPATIAL_NODE_CAPABILITIES"]) if os.getenv("SPATIAL_NODE_CAPABILITIES") else None))
    node_gateway_url: str = field(default_factory=lambda: os.getenv(
        "SPATIAL_NODE_GATEWAY_URL", "http://127.0.0.1:8000"))
    node_interval: float = 10
    node_job_timeout: float = 86400
    scene_previews_root: Path = field(
        default_factory=lambda: Path(os.getenv(
            "SPATIAL_SCENE_PREVIEWS_ROOT", "~/.spatial-sync/cache/scene-previews"
        )).expanduser()
    )
    live_bindings: Path | None = field(default_factory=lambda: (
        Path(os.environ["SPATIAL_LIVE_BINDINGS"]) if os.getenv("SPATIAL_LIVE_BINDINGS") else None
    ))

    def __post_init__(self):
        if self.recordings_keep < 1:
            raise ValueError("recordings_keep must be positive")
        if self.live_retention_days < 1:
            raise ValueError("live_retention_days must be positive")
        if self.remote and (not self.operator_token or not self.allowed_origins):
            raise ValueError("remote mode requires an operator token and explicit allowed origins")
        if min(self.heartbeat_interval_seconds, self.send_timeout_seconds, self.reliable_queue_size) <= 0:
            raise ValueError("heartbeat, send timeout and queue capacity must be positive")
