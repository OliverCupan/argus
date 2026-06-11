"""
Tests for the demo buggy app.

FIX 5: All tests now pass.  The missing-key test previously expected 400 but
        received 500 (unhandled KeyError).  /process now validates input and
        returns proper 400 errors.  A second test covers the null-body path
        (TypeError, not KeyError) that was previously untested.

Authentication: protected routes require the X-API-Key header.  The fixture
sets DEMO_API_KEY in the environment so tests can supply a known key.
"""

import os
import pytest

# Set a test API key before importing the app so os.getenv picks it up
os.environ.setdefault("DEMO_API_KEY", "test-key-for-pytest")

from app import app  # noqa: E402 — import after env var is set

TEST_API_KEY = os.environ["DEMO_API_KEY"]
AUTH = {"X-API-Key": TEST_API_KEY}


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


def test_health(client):
    """Health endpoint is unauthenticated — always passes."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.get_json()["status"] == "ok"


def test_process_valid(client):
    """Valid payload returns computed result."""
    response = client.post(
        "/process",
        json={"value": 5, "label": "test"},
        content_type="application/json",
        headers=AUTH,
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data["result"] == 10
    assert data["label"] == "TEST"


def test_process_missing_key(client):
    """FIX 5: Missing 'value' key now returns 400, not 500."""
    response = client.post(
        "/process",
        json={"label": "test"},  # missing 'value' key
        content_type="application/json",
        headers=AUTH,
    )
    assert response.status_code == 400
    assert "value" in response.get_json()["error"]


def test_process_null_body(client):
    """FIX 4a: Null / non-JSON body returns 400 (previously raised TypeError → 500)."""
    response = client.post(
        "/process",
        data="not json at all",
        content_type="text/plain",
        headers=AUTH,
    )
    assert response.status_code == 400
    assert "JSON" in response.get_json()["error"]


def test_process_no_auth(client):
    """Unauthenticated request to protected route returns 401."""
    response = client.post(
        "/process",
        json={"value": 1, "label": "x"},
        content_type="application/json",
    )
    assert response.status_code == 401


def test_average_valid(client):
    """Test /average with valid input."""
    response = client.post(
        "/average",
        json={"numbers": [1, 2, 3, 4, 5]},
        content_type="application/json",
        headers=AUTH,
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data["average"] == 3.0


def test_average_empty_list(client):
    """Test /average with empty list — should return 0."""
    response = client.post(
        "/average",
        json={"numbers": []},
        content_type="application/json",
        headers=AUTH,
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data["average"] == 0


def test_average_floats(client):
    """Test /average with float values."""
    response = client.post(
        "/average",
        json={"numbers": [1.5, 2.5, 3.5]},
        content_type="application/json",
        headers=AUTH,
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data["average"] == 2.5


def test_average_mixed_int_float(client):
    """Test /average with mixed int and float."""
    response = client.post(
        "/average",
        json={"numbers": [1, 2.5, 4]},
        content_type="application/json",
        headers=AUTH,
    )
    assert response.status_code == 200
    data = response.get_json()
    assert abs(data["average"] - 2.5) < 0.0001


def test_average_missing_numbers_key(client):
    """Test /average with missing 'numbers' key."""
    response = client.post(
        "/average",
        json={"other": [1, 2, 3]},
        content_type="application/json",
        headers=AUTH,
    )
    assert response.status_code == 400
    assert "Missing 'numbers' key" in response.get_json()["error"]


def test_average_invalid_json(client):
    """Test /average with invalid JSON."""
    response = client.post(
        "/average",
        data="not json",
        content_type="application/json",
        headers=AUTH,
    )
    assert response.status_code == 400


def test_average_not_a_list(client):
    """Test /average where 'numbers' is not a list."""
    response = client.post(
        "/average",
        json={"numbers": "not a list"},
        content_type="application/json",
        headers=AUTH,
    )
    assert response.status_code == 400
    assert "'numbers' must be a list" in response.get_json()["error"]


def test_average_contains_non_numeric(client):
    """Test /average with non-numeric items in list."""
    response = client.post(
        "/average",
        json={"numbers": [1, "two", 3]},
        content_type="application/json",
        headers=AUTH,
    )
    assert response.status_code == 400
    assert "numeric" in response.get_json()["error"]


def test_average_contains_bool(client):
    """Test /average with boolean in list (bool is subclass of int in Python)."""
    response = client.post(
        "/average",
        json={"numbers": [1, True, 3]},
        content_type="application/json",
        headers=AUTH,
    )
    assert response.status_code == 400
    assert "numeric" in response.get_json()["error"]


def test_average_single_value(client):
    """Test /average with single value."""
    response = client.post(
        "/average",
        json={"numbers": [42]},
        content_type="application/json",
        headers=AUTH,
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data["average"] == 42
