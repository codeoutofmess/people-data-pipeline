import pytest
from fastapi.testclient import TestClient

from people import auth, pipeline
from people.api import app

from .conftest import TEST_URL

TOKENS = {"admin_demo": "admin-test", "manager_demo": "manager-test", "viewer_demo": "viewer-test"}
MANAGER = "P0002"  # the Engineering lead


@pytest.fixture
def client(conn, raw_dir, monkeypatch):
    pipeline.run(conn, raw_dir)
    auth.seed_users(conn, TOKENS, MANAGER)
    monkeypatch.setenv("DATABASE_URL", TEST_URL)  # the API opens its own connections
    return TestClient(app)


@pytest.fixture
def my_report(conn):
    """Someone who reports directly to the demo manager."""
    sql = "SELECT person_id FROM employees WHERE manager_id = %s ORDER BY person_id LIMIT 1"
    return conn.execute(sql, (MANAGER,)).fetchone()[0]


@pytest.fixture
def other_team(conn):
    """An employee who does NOT report to the demo manager."""
    return conn.execute(
        "SELECT person_id FROM employees WHERE manager_id IS DISTINCT FROM %s"
        " AND person_id <> %s ORDER BY person_id DESC LIMIT 1",
        (MANAGER, MANAGER),
    ).fetchone()[0]


def get(client, path, user):
    return client.get(path, headers={"Authorization": f"Bearer {TOKENS[user]}"})


def test_missing_or_wrong_token_is_401_and_logged(client, conn):
    assert client.get("/insights/hiring-channels").status_code == 401
    bad = client.get("/insights/hiring-channels", headers={"Authorization": "Bearer nope"})
    assert bad.status_code == 401

    rows = conn.execute("SELECT username, action, person_id, outcome FROM audit_log").fetchall()
    assert rows == [("anonymous", "failed_login", None, "denied")] * 2
    assert "nope" not in str(conn.execute("SELECT * FROM audit_log").fetchall())  # no token kept


def test_admin_sees_whole_journey_in_date_order(client, my_report):
    body = get(client, f"/people/{my_report}/journey", "admin_demo").json()
    kinds = {e["event"] for e in body["events"]}
    assert {"applied", "hired", "started", "paid"} <= kinds
    dates = [e["date"] for e in body["events"]]
    assert dates == sorted(dates)


def test_manager_sees_direct_report_without_pay(client, my_report):
    r = get(client, f"/people/{my_report}/journey", "manager_demo")
    assert r.status_code == 200
    assert "paid" not in {e["event"] for e in r.json()["events"]}


def test_manager_cannot_see_another_team(client, other_team):
    assert get(client, f"/people/{other_team}/journey", "manager_demo").status_code == 403


def test_manager_cannot_see_pay(client, my_report, other_team):
    assert get(client, f"/people/{other_team}/pay", "manager_demo").status_code == 403
    assert (
        get(client, f"/people/{my_report}/pay", "manager_demo").status_code == 403
    )  # pay is admin only


def test_admin_can_see_pay(client, my_report):
    r = get(client, f"/people/{my_report}/pay", "admin_demo")
    assert r.status_code == 200 and r.json()["pay"]


def test_viewer_gets_no_person_data(client, my_report):
    assert get(client, f"/people/{my_report}/journey", "viewer_demo").status_code == 403
    assert get(client, f"/people/{my_report}/pay", "viewer_demo").status_code == 403


def test_unknown_person(client):
    assert get(client, "/people/P0000/journey", "admin_demo").status_code == 404
    # A manager gets 403, not 404, so they cannot probe who exists.
    assert get(client, "/people/P0000/journey", "manager_demo").status_code == 403


def test_every_person_read_is_audited(client, conn, my_report, other_team):
    get(client, f"/people/{my_report}/journey", "manager_demo")
    get(client, f"/people/{other_team}/pay", "manager_demo")
    get(client, f"/people/{my_report}/pay", "admin_demo")
    get(client, "/insights/hiring-channels", "viewer_demo")  # aggregate: not audited

    rows = conn.execute(
        "SELECT username, role, action, person_id, outcome, at FROM audit_log ORDER BY id"
    ).fetchall()
    assert [r[:5] for r in rows] == [
        ("manager_demo", "manager", "read_journey", my_report, "allowed"),
        ("manager_demo", "manager", "read_pay", other_team, "denied"),
        ("admin_demo", "people_admin", "read_pay", my_report, "allowed"),
    ]
    assert all(r[5] is not None for r in rows)  # "when"


def test_hiring_channel_insight(client):
    for user in TOKENS:
        assert get(client, "/insights/hiring-channels", user).status_code == 200
    body = get(client, "/insights/hiring-channels", "viewer_demo").json()
    assert "demo metric" in body["note"]
    assert {c["source_channel"] for c in body["channels"]} == {
        "referral", "job_board", "careers_site", "agency", "university",
    }  # fmt: skip
