"""Source-free loopback fixture for public Agent CLI/MCP metadata and transport."""

from pathlib import Path

from apps.api.app.main import create_app
from apps.api.app.platform_monitor import platform_snapshot, unavailable_system
from packages.library import agents


class FakeHostSampler:
    def sample(self, rows):
        return platform_snapshot(rows, unavailable_system("Host probes disabled in Agent transport fixture"))


source_starts = 0


def no_source(*args):
    global source_starts
    source_starts += 1
    raise AssertionError("Agent metadata and snapshots must not start a Source")


app = create_app(source_factory=no_source, platform_sampler=FakeHostSampler())


@app.get("/e2e/agent-origins")
async def origins():
    return {"agentLibrary": str(Path(agents.__file__).resolve()), "sourceStarts": source_starts,
            "roots": {name: str(root) for name, root in app.state.manager.library_assets.roots.items()},
            "resourcePaths": {
                "vault": str(app.state.settings.state_root / "vault"),
                "models": str(app.state.settings.state_root / "models.yaml"),
                "prices": str(app.state.settings.state_root / "prices.yaml"),
            }}


@app.get("/e2e/storage-roots")
async def storage_roots():
    return {"sceneLibrary": str(app.state.manager.library.roots["user"]),
            "assetLibrary": str(app.state.library_assets.roots["user"]),
            "state": str(app.state.settings.state_root.resolve()),
            "recordings": str(app.state.settings.recordings_root.resolve())}
