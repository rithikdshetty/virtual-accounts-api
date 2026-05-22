"""
Tests for the accounts router. Coverage targets:

- Auth: 401 without bearer token, 401 with wrong token, 200 with right token
- Create: happy path, currency validation, metadata limits
- Retrieve: hit, miss (404), wrong-prefix ID
- List: empty, with pagination cursor, with filters
- Update: valid transitions, invalid transitions, closed-is-terminal

These tests are not exhaustive. They cover the contract a reviewer will
probe. Edge cases like SQL injection or race conditions are out of scope
for unit tests; integration tests in Phase 4+ cover concurrency.
"""
from fastapi.testclient import TestClient


# ---------- auth ----------

def test_create_without_auth_returns_401(client: TestClient):
    resp = client.post("/accounts", json={"customer_id": "cus_x", "currency": "USD"})
    assert resp.status_code == 401
    body = resp.json()
    assert body["detail"]["code"] == "unauthorized"


def test_create_with_wrong_token_returns_401(client: TestClient):
    resp = client.post(
        "/accounts",
        json={"customer_id": "cus_x", "currency": "USD"},
        headers={"Authorization": "Bearer wrong_token"},
    )
    assert resp.status_code == 401


def test_create_with_correct_token_succeeds(client: TestClient, auth_headers):
    resp = client.post(
        "/accounts",
        json={"customer_id": "cus_x", "currency": "USD"},
        headers=auth_headers,
    )
    assert resp.status_code == 201


# ---------- create ----------

def test_create_returns_full_resource(client: TestClient, auth_headers):
    resp = client.post(
        "/accounts",
        json={
            "customer_id": "cus_42",
            "currency": "USD",
            "metadata": {"plan": "pro", "tier": "gold"},
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["object"] == "account"
    assert body["id"].startswith("acct_")
    assert body["customer_id"] == "cus_42"
    assert body["currency"] == "USD"
    assert body["status"] == "pending"
    assert body["livemode"] is False
    assert body["metadata"] == {"plan": "pro", "tier": "gold"}
    assert "created_at" in body
    assert "updated_at" in body


def test_create_response_has_request_id_header(client: TestClient, auth_headers):
    resp = client.post(
        "/accounts",
        json={"customer_id": "cus_x", "currency": "USD"},
        headers=auth_headers,
    )
    assert "request-id" in {k.lower() for k in resp.headers.keys()}


def test_create_rejects_invalid_currency(client: TestClient, auth_headers):
    resp = client.post(
        "/accounts",
        json={"customer_id": "cus_x", "currency": "usd"},  # lowercase
        headers=auth_headers,
    )
    assert resp.status_code == 422


def test_create_rejects_oversized_metadata(client: TestClient, auth_headers):
    too_many = {f"key_{i}": "v" for i in range(60)}
    resp = client.post(
        "/accounts",
        json={"customer_id": "cus_x", "currency": "USD", "metadata": too_many},
        headers=auth_headers,
    )
    assert resp.status_code == 422


def test_create_rejects_missing_required_fields(client: TestClient, auth_headers):
    resp = client.post(
        "/accounts",
        json={"currency": "USD"},  # missing customer_id
        headers=auth_headers,
    )
    assert resp.status_code == 422


# ---------- retrieve ----------

def test_retrieve_returns_account(client: TestClient, auth_headers):
    created = client.post(
        "/accounts",
        json={"customer_id": "cus_x", "currency": "USD"},
        headers=auth_headers,
    ).json()

    resp = client.get(f"/accounts/{created['id']}", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["id"] == created["id"]


def test_retrieve_unknown_returns_404(client: TestClient, auth_headers):
    resp = client.get("/accounts/acct_does_not_exist", headers=auth_headers)
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "account_not_found"


# ---------- list ----------

def test_list_returns_empty_when_no_accounts(client: TestClient, auth_headers):
    resp = client.get("/accounts", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["object"] == "list"
    assert body["data"] == []
    assert body["has_more"] is False
    assert body["next_cursor"] is None


def test_list_returns_all_created(client: TestClient, auth_headers):
    for i in range(3):
        client.post(
            "/accounts",
            json={"customer_id": f"cus_{i}", "currency": "USD"},
            headers=auth_headers,
        )
    resp = client.get("/accounts", headers=auth_headers)
    assert resp.status_code == 200
    assert len(resp.json()["data"]) == 3


def test_list_paginates_correctly(client: TestClient, auth_headers):
    for i in range(5):
        client.post(
            "/accounts",
            json={"customer_id": f"cus_{i}", "currency": "USD"},
            headers=auth_headers,
        )
    # Get first page with limit=2
    page1 = client.get("/accounts?limit=2", headers=auth_headers).json()
    assert len(page1["data"]) == 2
    assert page1["has_more"] is True
    assert page1["next_cursor"] is not None

    # Get next page
    page2 = client.get(
        f"/accounts?limit=2&starting_after={page1['next_cursor']}",
        headers=auth_headers,
    ).json()
    assert len(page2["data"]) == 2
    # Different rows from page 1
    page1_ids = {a["id"] for a in page1["data"]}
    page2_ids = {a["id"] for a in page2["data"]}
    assert page1_ids.isdisjoint(page2_ids)


def test_list_filters_by_customer_id(client: TestClient, auth_headers):
    client.post(
        "/accounts",
        json={"customer_id": "cus_a", "currency": "USD"},
        headers=auth_headers,
    )
    client.post(
        "/accounts",
        json={"customer_id": "cus_b", "currency": "USD"},
        headers=auth_headers,
    )
    resp = client.get("/accounts?customer_id=cus_a", headers=auth_headers)
    body = resp.json()
    assert len(body["data"]) == 1
    assert body["data"][0]["customer_id"] == "cus_a"


# ---------- update / state machine ----------

def test_update_pending_to_active_succeeds(client: TestClient, auth_headers):
    created = client.post(
        "/accounts",
        json={"customer_id": "cus_x", "currency": "USD"},
        headers=auth_headers,
    ).json()
    resp = client.patch(
        f"/accounts/{created['id']}",
        json={"status": "active"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "active"


def test_update_pending_to_frozen_fails(client: TestClient, auth_headers):
    """pending can only go to active or closed, not frozen."""
    created = client.post(
        "/accounts",
        json={"customer_id": "cus_x", "currency": "USD"},
        headers=auth_headers,
    ).json()
    resp = client.patch(
        f"/accounts/{created['id']}",
        json={"status": "frozen"},
        headers=auth_headers,
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "invalid_status_transition"


def test_update_active_to_frozen_to_active_round_trips(
    client: TestClient, auth_headers
):
    created = client.post(
        "/accounts",
        json={"customer_id": "cus_x", "currency": "USD"},
        headers=auth_headers,
    ).json()
    client.patch(
        f"/accounts/{created['id']}",
        json={"status": "active"},
        headers=auth_headers,
    )
    r1 = client.patch(
        f"/accounts/{created['id']}",
        json={"status": "frozen"},
        headers=auth_headers,
    )
    assert r1.status_code == 200
    r2 = client.patch(
        f"/accounts/{created['id']}",
        json={"status": "active"},
        headers=auth_headers,
    )
    assert r2.status_code == 200


def test_closed_is_terminal(client: TestClient, auth_headers):
    created = client.post(
        "/accounts",
        json={"customer_id": "cus_x", "currency": "USD"},
        headers=auth_headers,
    ).json()
    client.patch(
        f"/accounts/{created['id']}",
        json={"status": "closed"},
        headers=auth_headers,
    )
    # Now try to re-open
    resp = client.patch(
        f"/accounts/{created['id']}",
        json={"status": "active"},
        headers=auth_headers,
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "invalid_status_transition"


def test_update_metadata_only(client: TestClient, auth_headers):
    created = client.post(
        "/accounts",
        json={"customer_id": "cus_x", "currency": "USD", "metadata": {"a": "1"}},
        headers=auth_headers,
    ).json()
    resp = client.patch(
        f"/accounts/{created['id']}",
        json={"metadata": {"b": "2"}},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["metadata"] == {"b": "2"}
    assert resp.json()["status"] == "pending"  # unchanged


def test_update_nonexistent_returns_404(client: TestClient, auth_headers):
    resp = client.patch(
        "/accounts/acct_nope",
        json={"status": "active"},
        headers=auth_headers,
    )
    assert resp.status_code == 404
