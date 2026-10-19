"""Public response and authentication boundaries around HTTP middleware."""

import pytest
from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from apps.api.app.settings import Settings

ORIGIN = "http://allowed.test"


@pytest.mark.parametrize(
    "remote,token,status",
    [
        (False, None, 422),
        (False, "invalid", 422),
        (True, None, 401),
        (True, "viewer", 403),
        (True, "operator", 422),
    ],
)
def test_authentication_precedes_body_validation_and_preserves_json_headers(tmp_path, remote, token, status):
    settings = Settings(
        remote=remote,
        operator_token="operator",
        viewer_token="viewer",
        allowed_origins=(ORIGIN,),
        recordings_root=tmp_path / "recordings",
    )
    headers = {"Origin": ORIGIN, "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    with TestClient(create_app(settings)) as client:
        response = client.post("/api/v1/sessions", content=b'{"sceneId":', headers=headers)
    assert response.status_code == status
    assert response.headers["content-type"] == "application/json"
    assert int(response.headers["content-length"]) == len(response.content)
    assert response.headers["access-control-allow-origin"] == ORIGIN
    if status in (401, 403):
        assert response.json() == {"detail": {"code": "unauthorized"}}
    else:
        assert response.json()["detail"][0]["type"] == "json_invalid"


@pytest.mark.parametrize("remote", [False, True])
@pytest.mark.parametrize("method,path", [("GET", "/api/v1/scenes"), ("PUT", "/api/v1/prices")])
def test_cors_preflight_remains_available_before_authentication(tmp_path, remote, method, path):
    settings = Settings(
        remote=remote,
        operator_token="operator",
        allowed_origins=(ORIGIN,),
        recordings_root=tmp_path / "recordings",
    )
    with TestClient(create_app(settings)) as client:
        response = client.options(
            path,
            headers={
                "Origin": ORIGIN,
                "Access-Control-Request-Method": method,
                "Access-Control-Request-Headers": "Authorization, X-Actor",
            },
        )
    assert response.status_code == 200 and response.content == b"OK"
    assert response.headers["access-control-allow-origin"] == ORIGIN
    assert response.headers["access-control-allow-methods"] == "GET, POST, PUT, DELETE"
    assert {"authorization", "x-actor"}.issubset(
        {header.strip().lower() for header in response.headers["access-control-allow-headers"].split(",")}
    )
