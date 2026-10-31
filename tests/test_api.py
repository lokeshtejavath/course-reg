"""End-to-end HTTP tests: status codes, error envelope and request validation.

Seeded learners (see conftest ``client``) are referred to by username; the
``ids`` fixture maps them to their server-generated UUIDs.
"""

import uuid

import pytest


@pytest.fixture
def oid(client):
    r = client.post("/offerings", json={"code": "cs201", "title": "Data Structures", "capacity": 1, "prerequisites": ["CS101"]})
    assert r.status_code == 201
    body = r.json()
    assert body["code"] == "CS201"
    assert body["seats_available"] == 1
    return body["id"]


@pytest.fixture
def ids(client):
    return {u: client.get(f"/learners/by-username/{u}").json()["id"] for u in "ABCD"}


def enroll(client, oid, learner_id):
    return client.post(f"/offerings/{oid}/enrollments", json={"learner_id": learner_id})


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_full_flow_enroll_waitlist_drop_promote(client, oid, ids):
    r = enroll(client, oid, ids["A"])
    assert r.status_code == 201
    assert r.json()["status"] == "ENROLLED"

    r = enroll(client, oid, ids["B"])
    assert r.status_code == 201
    assert r.json() == {
        "offering_id": oid,
        "learner_id": ids["B"],
        "username": "B",
        "status": "WAITLISTED",
        "waitlist_position": 1,
        "learner_created": False,
    }

    r = client.delete(f"/offerings/{oid}/enrollments/{ids['A']}")
    assert r.status_code == 200
    assert r.json()["promoted_learner_ids"] == [ids["B"]]

    state = client.get(f"/offerings/{oid}").json()
    assert state["enrolled_learner_ids"] == [ids["B"]]
    assert state["waitlist"] == []


def test_duplicate_enroll_returns_409_with_status(client, oid, ids):
    enroll(client, oid, ids["A"])
    r = enroll(client, oid, ids["A"])
    assert r.status_code == 409
    err = r.json()["error"]
    assert err["code"] == "ALREADY_ENROLLED"
    assert err["details"]["current_status"] == "ENROLLED"

    enroll(client, oid, ids["B"])
    r = enroll(client, oid, ids["B"])
    assert r.json()["error"]["code"] == "ALREADY_WAITLISTED"
    assert r.json()["error"]["details"]["waitlist_position"] == 1


def test_prerequisite_failure_returns_422(client, oid, ids):
    r = enroll(client, oid, ids["D"])
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "PREREQUISITES_NOT_MET"
    assert err["details"]["missing_prerequisites"] == ["CS101"]


def test_drop_errors(client, oid, ids):
    enroll(client, oid, ids["A"])
    enroll(client, oid, ids["B"])

    assert client.delete(f"/offerings/{oid}/enrollments/{ids['C']}").json()["error"]["code"] == "NOT_REGISTERED"
    r = client.delete(f"/offerings/{oid}/enrollments/{ids['B']}")
    assert (r.status_code, r.json()["error"]["code"]) == (409, "NOT_ENROLLED")

    client.delete(f"/offerings/{oid}/enrollments/{ids['A']}")
    r = client.delete(f"/offerings/{oid}/enrollments/{ids['A']}")
    assert (r.status_code, r.json()["error"]["code"]) == (409, "ALREADY_DROPPED")

    r = enroll(client, oid, ids["A"])
    assert (r.status_code, r.json()["error"]["code"]) == (409, "REENROLLMENT_NOT_ALLOWED")


def test_leave_waitlist(client, oid, ids):
    enroll(client, oid, ids["A"])
    enroll(client, oid, ids["B"])
    r = client.delete(f"/offerings/{oid}/waitlist/{ids['B']}")
    assert r.status_code == 200
    assert r.json()["status"] == "WITHDRAWN"
    r = client.delete(f"/offerings/{oid}/waitlist/{ids['B']}")
    assert r.json()["error"]["code"] == "ALREADY_WITHDRAWN"
    r = client.delete(f"/offerings/{oid}/waitlist/{ids['A']}")
    assert r.json()["error"]["code"] == "NOT_WAITLISTED"


def test_not_found(client, oid, ids):
    assert client.get("/offerings/999").status_code == 404
    r = enroll(client, 999, ids["A"])
    assert (r.status_code, r.json()["error"]["code"]) == (404, "OFFERING_NOT_FOUND")
    r = enroll(client, oid, str(uuid.uuid4()))
    assert (r.status_code, r.json()["error"]["code"]) == (404, "LEARNER_NOT_FOUND")


# ------------------------------------------------------------------- learners


def test_create_learner_assigns_uuid_and_normalizes_courses(client):
    r = client.post("/learners", json={"username": "jdoe", "name": "Jane Doe", "completed_courses": ["cs101", " MATH101 "]})
    assert r.status_code == 201
    body = r.json()
    assert uuid.UUID(body["id"])  # server-generated
    assert body["username"] == "jdoe"
    assert body["completed_courses"] == ["CS101", "MATH101"]

    assert client.get(f"/learners/{body['id']}").json() == body
    assert client.get("/learners/by-username/jdoe").json() == body


def test_create_learner_rejects_taken_username(client):
    r = client.post("/learners", json={"username": "A", "name": "Another A"})
    assert (r.status_code, r.json()["error"]["code"]) == (409, "USERNAME_TAKEN")


def test_client_cannot_choose_learner_id(client):
    forced = str(uuid.uuid4())
    r = client.post("/learners", json={"id": forced, "username": "x1", "name": "X"})
    assert r.status_code == 201
    assert r.json()["id"] != forced


def test_new_learner_can_join_waitlist_any_time(client, oid, ids):
    enroll(client, oid, ids["A"])  # fills the only seat
    new_id = client.post("/learners", json={"username": "late", "name": "Late", "completed_courses": ["CS101"]}).json()["id"]

    r = enroll(client, oid, new_id)

    assert r.status_code == 201
    assert (r.json()["status"], r.json()["waitlist_position"]) == ("WAITLISTED", 1)
    assert client.get(f"/offerings/{oid}").json()["waitlist"][0]["username"] == "late"


def test_enroll_inline_creates_learner_and_waitlists(client, oid, ids):
    enroll(client, oid, ids["A"])

    r = client.post(
        f"/offerings/{oid}/enrollments",
        json={"learner": {"username": "walkin", "name": "Walk In", "completed_courses": ["CS101"]}},
    )

    assert r.status_code == 201
    body = r.json()
    assert body["learner_created"] is True
    assert (body["username"], body["status"], body["waitlist_position"]) == ("walkin", "WAITLISTED", 1)
    assert client.get("/learners/by-username/walkin").json()["id"] == body["learner_id"]


def test_enroll_inline_with_existing_username_uses_existing_learner(client, oid, ids):
    r = client.post(
        f"/offerings/{oid}/enrollments",
        json={"learner": {"username": "D", "name": "ignored", "completed_courses": ["CS101"]}},
    )
    # Existing learner D is used as stored (no CS101); supplied courses are not applied.
    assert (r.status_code, r.json()["error"]["code"]) == (422, "PREREQUISITES_NOT_MET")
    assert client.get(f"/learners/{ids['D']}").json()["completed_courses"] == []


def test_enroll_inline_rejected_does_not_create_learner(client, oid):
    r = client.post(
        f"/offerings/{oid}/enrollments",
        json={"learner": {"username": "noprereq", "name": "N", "completed_courses": []}},
    )
    assert r.json()["error"]["code"] == "PREREQUISITES_NOT_MET"
    assert client.get("/learners/by-username/noprereq").status_code == 404


def test_update_completed_courses_changes_waitlist_eligibility(client, oid, ids):
    enroll(client, oid, ids["A"])
    enroll(client, oid, ids["B"])  # waitlisted, eligible
    enroll(client, oid, ids["C"])  # waitlisted, eligible

    r = client.put(f"/learners/{ids['B']}/completed-courses", json={"completed_courses": []})
    assert r.status_code == 200
    waitlist = client.get(f"/offerings/{oid}").json()["waitlist"]
    assert [(e["username"], e["position"], e["eligible"]) for e in waitlist] == [("B", 1, False), ("C", 2, True)]

    # B is skipped but retained; C is promoted.
    r = client.delete(f"/offerings/{oid}/enrollments/{ids['A']}")
    assert r.json()["promoted_learner_ids"] == [ids["C"]]

    # B regains eligibility: no immediate promotion, keeps position 1.
    client.put(f"/learners/{ids['B']}/completed-courses", json={"completed_courses": ["CS101"]})
    state = client.get(f"/offerings/{oid}").json()
    assert state["enrolled_learner_ids"] == [ids["C"]]
    assert [(e["username"], e["eligible"]) for e in state["waitlist"]] == [("B", True)]


def test_update_completed_courses_unknown_learner(client):
    r = client.put(f"/learners/{uuid.uuid4()}/completed-courses", json={"completed_courses": []})
    assert (r.status_code, r.json()["error"]["code"]) == (404, "LEARNER_NOT_FOUND")


# ----------------------------------------------------------------- validation


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"learner_id": ""},
        {"learner_id": "A"},  # not a UUID
        {"learner_id": 123},
        {"learner_id": "3f1c2a4e-0000-4000-8000-000000000000", "learner": {"username": "u", "name": "U"}},
        {"learner": {"username": "has space", "name": "U"}},
        {"learner": {"username": "u", "name": "  "}},
        {"learner": {"username": "u", "name": "U", "completed_courses": ["bad code"]}},
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


def test_invalid_path_ids(client, oid):
    assert client.get("/offerings/0").status_code == 422
    assert client.get("/offerings/abc").status_code == 422
    assert client.get("/learners/not-a-uuid").status_code == 422
    assert client.delete(f"/offerings/{oid}/enrollments/A").status_code == 422
