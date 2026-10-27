"""Pending approvals remain reachable beyond the legacy one hundred rows."""

from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from packages.protocol.schema import Actor


def test_counts_are_lightweight_and_cursor_keeps_older_pending_reachable(monkeypatch):
    app = create_app()
    with TestClient(app) as client:
        store = app.state.manager.approvals
        for index in range(101):
            store.submit(
                subject="invocation",
                subject_id=f"test-{index}",
                title=f"Test {index}",
                requester=Actor(kind="user", name="test"),
            )
        assert len(client.get("/api/v1/approvals").json()["approvals"]) == 100
        summary = client.get("/api/v1/approvals?view=summary").json()
        assert summary == {"approvals": [], "pendingCount": 101, "role": "operator"}
        seen, cursor = [], None
        while True:
            page = client.get(
                "/api/v1/approvals",
                params={
                    "view": "page",
                    "status": "pending",
                    "limit": 20,
                    **({"cursor": cursor} if cursor else {}),
                },
            ).json()
            rows = page["approvals"]
            seen.extend(value["approvalId"] for value in rows)
            # A decision removes an item from pending; it must not shift an offset past older records.
            store.set_status(rows[0]["approvalId"], "unavailable")
            cursor = page["pagination"]["nextCursor"]
            if not cursor:
                break
        assert len(seen) == len(set(seen)) == 101
        response = client.get(
            "/api/v1/approvals",
            params={
                "view": "page",
                "status": "processed",
                "include": seen[0],
                "limit": 20,
            },
        ).json()
        assert response["recentApprovals"][0]["approvalId"] == seen[0]
        assert client.get("/api/v1/approvals?view=page&cursor=invalid").status_code == 422
