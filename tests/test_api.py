"""End-to-end HTTP tests: status codes, error envelope and request validation."""

import pytest


@pytest.fixture
def oid(client):
    r = client.post("/offerings", json={"code": "cs201", "title": "Data Structures", "capacity": 1, "prerequisites": ["CS101"]})
    assert r.status_code == 201
    body = r.json()
    assert body["code"] == "CS201"
    assert body["seats_available"] == 1
    return body["id"]


def enroll(client, oid, learner_id):
    return client.post(f"/offerings/{oid}/enrollments", json={"learner_id": learner_id})


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_full_flow_enroll_waitlist_drop_promote(client, oid):
    r = enroll(client, oid, "A")
    assert r.status_code == 201
    assert r.json()["status"] == "ENROLLED"

    r = enroll(client, oid, "B")
    assert r.status_code == 201
    assert r.json() == {"offering_id": oid, "learner_id": "B", "status": "WAITLISTED", "waitlist_position": 1}

    r = client.delete(f"/offerings/{oid}/enrollments/A")
    assert r.status_code == 200
    assert r.json()["promoted_learner_ids"] == ["B"]

    state = client.get(f"/offerings/{oid}").json()
    assert state["enrolled_learner_ids"] == ["B"]
    assert state["waitlist"] == []


def test_duplicate_enroll_returns_409_with_status(client, oid):
    enroll(client, oid, "A")
    r = enroll(client, oid, "A")
    assert r.status_code == 409
    err = r.json()["error"]
    assert err["code"] == "ALREADY_ENROLLED"
    assert err["details"]["current_status"] == "ENROLLED"

    enroll(client, oid, "B")
    r = enroll(client, oid, "B")
    assert r.json()["error"]["code"] == "ALREADY_WAITLISTED"
    assert r.json()["error"]["details"]["waitlist_position"] == 1


def test_prerequisite_failure_returns_422(client, oid):
    r = enroll(client, oid, "D")
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "PREREQUISITES_NOT_MET"
    assert err["details"]["missing_prerequisites"] == ["CS101"]


def test_drop_errors(client, oid):
    enroll(client, oid, "A")
    enroll(client, oid, "B")

    assert client.delete(f"/offerings/{oid}/enrollments/C").json()["error"]["code"] == "NOT_REGISTERED"
    r = client.delete(f"/offerings/{oid}/enrollments/B")
    assert (r.status_code, r.json()["error"]["code"]) == (409, "NOT_ENROLLED")

    client.delete(f"/offerings/{oid}/enrollments/A")
    r = client.delete(f"/offerings/{oid}/enrollments/A")
    assert (r.status_code, r.json()["error"]["code"]) == (409, "ALREADY_DROPPED")

    r = enroll(client, oid, "A")
    assert (r.status_code, r.json()["error"]["code"]) == (409, "REENROLLMENT_NOT_ALLOWED")


def test_leave_waitlist(client, oid):
    enroll(client, oid, "A")
    enroll(client, oid, "B")
    r = client.delete(f"/offerings/{oid}/waitlist/B")
    assert r.status_code == 200
    assert r.json()["status"] == "WITHDRAWN"
    r = client.delete(f"/offerings/{oid}/waitlist/B")
    assert r.json()["error"]["code"] == "ALREADY_WITHDRAWN"
    r = client.delete(f"/offerings/{oid}/waitlist/A")
    assert r.json()["error"]["code"] == "NOT_WAITLISTED"


def test_not_found(client, oid):
    assert client.get("/offerings/999").status_code == 404
    r = enroll(client, 999, "A")
    assert (r.status_code, r.json()["error"]["code"]) == (404, "OFFERING_NOT_FOUND")
    r = enroll(client, oid, "GHOST")
    assert (r.status_code, r.json()["error"]["code"]) == (404, "LEARNER_NOT_FOUND")


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"learner_id": ""},
        {"learner_id": "has space"},
        {"learner_id": 123},
        {"learner_id": "x" * 65},
    ],
)
def test_invalid_enroll_body_returns_422(client, oid, body):
    r = client.post(f"/offerings/{oid}/enrollments", json=body)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "INVALID_REQUEST"
    assert client.get(f"/offerings/{oid}").json()["enrolled_learner_ids"] == []


@pytest.mark.parametrize(
    "body",
    [
        {"code": "X1", "title": "T", "capacity": 0},
        {"code": "X1", "title": "T", "capacity": -3},
        {"code": "", "title": "T", "capacity": 1},
        {"code": "X1", "title": "   ", "capacity": 1},
        {"code": "X1", "title": "T", "capacity": 1, "prerequisites": ["bad code"]},
    ],
)
def test_invalid_offering_returns_422(client, body):
    r = client.post("/offerings", json=body)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "INVALID_REQUEST"


def test_duplicate_offering_code_returns_409(client, oid):
    r = client.post("/offerings", json={"code": "CS201", "title": "Dup", "capacity": 2})
    assert (r.status_code, r.json()["error"]["code"]) == (409, "OFFERING_CODE_EXISTS")


def test_invalid_offering_id_path(client):
    assert client.get("/offerings/0").status_code == 422
    assert client.get("/offerings/abc").status_code == 422
