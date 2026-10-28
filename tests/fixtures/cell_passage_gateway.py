"""Explicit actuator fixture with the real HTTP/Plan/recording services, never Newton proof.

Launch with owned roots: uvicorn --factory tests.fixtures.cell_passage_gateway:fixture_app
"""

import asyncio
import os
from pathlib import Path
from time import monotonic

from apps.api.app.main import create_app
from apps.api.app.settings import Settings
from apps.api.app.sources.synthetic_source import SyntheticSource
from packages.info.program import ProgramConfig
from packages.protocol.schema import SceneManifest
from tests.fixtures.cell_passage import build_workcell_host


class PublicLabwareFacts:
    """Synthetic producer of the real labware property contract; no spatial proof."""

    id, type, tick_priority = "labware.facts.fixture", "labware/v1", 1

    class Config(ProgramConfig):
        station: str = "/imager"
        location: str = "/imager"
        settled: bool = True
        lidClosed: bool = False

    def init(self, ctx):
        ctx.set(**ctx.config.model_dump())


def create_fixture_app(root):
    root = Path(root)

    class WorkcellSource(SyntheticSource):
        async def start(self, scene_id, options):
            self.options = options.model_copy()
            host_root = root / self.session_id
            host_root.mkdir(parents=True)
            self.host, self.actuator = build_workcell_host(
                host_root,
                vessels=[{"id": "source", "entityKey": "/peer", "site": "/sourceSite",
                          "capacityUl": 1000, "volumeUl": 500, "reagent": "medium"}],
                extra_bindings=[{"key": "/peer", "type": PublicLabwareFacts.type,
                                 "program": PublicLabwareFacts.id, "config": {}}],
                extra_programs=[PublicLabwareFacts],
                extra_types=[Path("devices/types/labware.v1.yaml").read_text(encoding="utf8")],
            )
            self.catalog = self.host.catalog
            self.manifest = SceneManifest(
                sessionId=self.session_id, sceneRevision="fixture-" + self.session_id,
                revisionNumericId=1, sceneName="Cell Passage HTTP fixture",
                source={"kind": "synthetic", "physicsDt": options.physicsDt},
                nodes=[{"kind": "group", "entityKey": key, "name": key} for key in ("/workcell", "/peer")],
            )
            self.catalog.sceneRevision = self.manifest.sceneRevision
            self._state = "RUNNING"
            self._publish("stream.snapshot")
            self._last_clock = monotonic()
            self._task = asyncio.create_task(self._run(), name=f"passage-fixture-{self.session_id}")
            return self.manifest, self.catalog

        def _poses(self):
            return []

        def _advance(self, count):
            self._step_index += count
            due = int(self.sim_time * self.options.controlHz + 1e-9)
            while self._control_index < due:
                self._control_index += 1
                for event in self.host.tick(
                    self._control_index / self.options.controlHz, 1 / self.options.controlHz
                ):
                    self._events.put_nowait(event)

    settings = Settings(recordings_root=root / "recordings", state_root=root / "state", node_trainer=())
    return create_app(settings, source_factory=lambda kind, sid: WorkcellSource(sid))


def fixture_app():
    root = Path(os.getenv("SPATIAL_STATE_ROOT", ".scratch/cell-passage/http-fixture/state")).resolve().parent
    os.environ.setdefault("LW_LIBRARY", str(root / "library"))
    return create_fixture_app(root)
