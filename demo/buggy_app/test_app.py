"""
Tests for the demo buggy app.

BUG 5: test_process_missing_key tests the None handling bug
       but is itself incorrectly written — it expects a 400 but
       the app will crash with a 500 (KeyError).
"""

import pytest
from app import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


def test_health(client):
    """This test passes."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.get_json()["status"] == "ok"


def test_process_valid(client):
    """This test passes."""
    response = client.post(
        "/process",
        json={"value": 5, "label": "test"},
        content_type="application/json",
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data["result"] == 10
    assert data["label"] == "TEST"


def test_process_missing_key(client):
    """BUG 5: This test FAILS — app crashes with KeyError, returns 500 not 400."""
    response = client.post(
        "/process",
        json={"label": "test"},  # missing 'value' key
        content_type="application/json",
    )
    assert response.status_code == 400  # expects 400, gets 500
