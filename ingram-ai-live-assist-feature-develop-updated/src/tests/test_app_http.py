"""HTTP contract the AgentCore Runtime relies on: /ping and JSON acks on /invocations."""

from __future__ import annotations

from starlette.testclient import TestClient

import main


def test_ping_endpoint_healthy():
    response = TestClient(main.app).get("/ping")
    assert response.status_code == 200
    assert response.json()["status"] in ("Healthy", "HealthyBusy")


def test_invalid_event_returns_json_rejection():
    response = TestClient(main.app).post("/invocations", json={"type": "turn"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    body = response.json()
    assert body["accepted"] is False and body["code"] == "invalid_event"


def test_session_header_mismatch_rejected():
    sid = "test-call-0001-abcdefghijklmnopqrstuvwxyz"
    response = TestClient(main.app).post(
        "/invocations",
        json={"schema": "ila.event.v1", "type": "session.start", "session_id": sid},
        headers={"X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": "other-" + sid},
    )
    assert response.json()["code"] == "session_mismatch"
