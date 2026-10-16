import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    recordings_root: Path = field(
        default_factory=lambda: Path(os.getenv("SPATIAL_RECORDINGS_ROOT", "recordings"))
    )
    recordings_keep: int = field(default_factory=lambda: int(os.getenv("SPATIAL_RECORDINGS_KEEP", "20")))
    recordings_enabled: bool = field(
        default_factory=lambda: os.getenv("SPATIAL_RECORDINGS_ENABLED", "1") != "0"
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

    def __post_init__(self):
        if self.recordings_keep < 1:
            raise ValueError("recordings_keep must be positive")
        if self.remote and (not self.operator_token or not self.allowed_origins):
            raise ValueError("remote mode requires an operator token and explicit allowed origins")
        if min(self.heartbeat_interval_seconds, self.send_timeout_seconds, self.reliable_queue_size) <= 0:
            raise ValueError("heartbeat, send timeout and queue capacity must be positive")
