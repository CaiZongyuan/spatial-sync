"""HTTP read semantics independent of Source scheduling and transport load."""

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api.app.info_routes import install_info_routes
from packages.info.invocations import InvocationTable
from packages.info.store import StateStore
from packages.protocol.schema import Catalog, EntityObservations, EntitySnapshot, Invocation

KEY = '/World/quoted-"-\u03b1'


def fixture():
    store = StateStore("scene")
    store.apply(
        EntitySnapshot(
            sceneRevision="scene",
            epoch=0,
            sampleTimeMs=100,
            entities=[
                {
                    "key": KEY,
                    "rev": 1,
                    "values": {"nested": {"nil": None, "items": [1]}, "huge": 10**100},
                }
            ],
        )
    )
    catalog = Catalog(
        sceneRevision="scene",
        entities={KEY: {"key": KEY, "type": "equipment/v1", "program": "bench.virtual"}},
        types={"equipment/v1": {"type": "equipment/v1", "title": "Equipment", "properties": {}}},
    )
    session = SimpleNamespace(
        info=store,
        catalog=SimpleNamespace(**catalog.model_dump(mode="json")),
        pipeline=SimpleNamespace(table=InvocationTable()),
    )
    app = FastAPI()
    install_info_routes(app, SimpleNamespace(get=lambda _: session), lambda _: "operator")
    return app, session


def test_state_and_description_escape_keys_preserve_numbers_and_deduplicate_selected_keys():
    app, session = fixture()
    with TestClient(app) as client:
        route = "/api/v1/sessions/session-read"
        response = client.get(route + "/state", params={"keys": KEY + "," + KEY})
        assert response.status_code == 200
        assert response.text.count('"key":') == 1
        state = response.json()["entities"][KEY]
        assert state == {
            "key": KEY,
            "type": "equipment/v1",
            "rev": 1,
            "values": {"nested": {"nil": None, "items": [1]}, "huge": 10**100},
            "meta": {},
            "active": [],
        }
        state["values"]["nested"]["items"].append(99)
        description = client.get(route + "/entity", params={"key": KEY})
        assert description.status_code == 200
        assert description.json()["state"]["values"]["nested"]["items"] == [1]
        assert description.json()["state"]["values"]["huge"] == 10**100
        for path in ("/state", "/entity"):
            missing = client.get(route + path, params={"keys" if path == "/state" else "key": "/unknown"})
            assert missing.status_code == 404 and missing.json()["detail"]["code"] == "unknown_entity"


def test_cached_properties_keep_active_ids_live_and_clear_on_epoch_and_catalog_change():
    app, session = fixture()
    route = "/api/v1/sessions/session-read/state"
    with TestClient(app) as client:
        assert client.get(route).json()["entities"][KEY]["active"] == []
        invocation = Invocation(
            invocationId="inv-live",
            requestId="request-live",
            entityKey=KEY,
            action="inspect",
            epoch=0,
            createdSimTime=0,
            createdAtMs=1,
            status="accepted",
        )
        session.pipeline.table.apply(invocation)
        assert client.get(route).json()["entities"][KEY]["active"] == ["inv-live"]
        session.pipeline.table.apply(invocation.model_copy(update={"status": "succeeded"}))
        assert client.get(route).json()["entities"][KEY]["active"] == []
        session.info.apply(
            EntitySnapshot(
                sceneRevision="scene",
                epoch=1,
                sampleTimeMs=300,
                entities=[{"key": KEY, "rev": 0, "values": {"answer": 5}}],
            )
        )
        assert client.get(route).json()["entities"][KEY]["values"] == {"answer": 5}
        assert not session.info.apply(
            EntityObservations(
                sceneRevision="scene",
                epoch=0,
                simTime=0,
                sampleTimeMs=400,
                source="virtual",
                changes=[{"key": KEY, "rev": 99, "values": {"answer": 99}}],
            )
        )
        assert client.get(route).json()["entities"][KEY]["values"] == {"answer": 5}
        session.info.configure("next")
        session.catalog.entities[KEY]["type"] = "equipment/v2"
        assert client.get(route).json()["entities"][KEY] == {
            "key": KEY,
            "type": "equipment/v2",
            "rev": 0,
            "values": {},
            "meta": {},
            "active": [],
        }
