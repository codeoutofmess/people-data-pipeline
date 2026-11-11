import importlib.util
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

from people import auth, pipeline
from people.api import app

from .conftest import TEST_URL

TOKENS = {
    "admin_demo": "admin-test",
    "manager_demo": "manager-test",
    "viewer_demo": "viewer-test",
    "employee_demo": "employee-test",
}
MANAGER = "P0002"  # the Engineering lead
EMPLOYEE = "P0009"  # one of the Engineering lead's direct reports


@pytest.fixture
def client(conn, raw_dir, monkeypatch):
    pipeline.run(conn, raw_dir)
    auth.seed_users(conn, TOKENS, MANAGER, EMPLOYEE)
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


def get(client, path, user, reason="team_planning"):
    params = {"reason": reason} if reason else {}
    return client.get(path, params=params, headers={"Authorization": f"Bearer {TOKENS[user]}"})


def test_missing_or_wrong_token_is_401_and_logged(client, conn):
    assert client.get("/insights/hiring-channels").status_code == 401
    bad = client.get("/insights/hiring-channels", headers={"Authorization": "Bearer nope"})
    assert bad.status_code == 401

    rows = conn.execute("SELECT username, action, person_id, outcome FROM audit_log").fetchall()
    assert rows == [("anonymous", "failed_login", None, "denied")] * 2
    assert "nope" not in str(conn.execute("SELECT * FROM audit_log").fetchall())  # no token kept


def test_expired_token_is_refused(client, conn):
    assert get(client, "/insights/hiring-channels", "viewer_demo").status_code == 200
    conn.execute("UPDATE api_users SET expires_at = now() - interval '1 second'")
    conn.commit()
    assert get(client, "/insights/hiring-channels", "viewer_demo").status_code == 401


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
        "SELECT username, role, action, person_id, outcome, reason, at FROM audit_log ORDER BY id"
    ).fetchall()
    assert [r[:6] for r in rows] == [
        ("manager_demo", "manager", "read_journey", my_report, "allowed", "team_planning"),
        ("manager_demo", "manager", "read_pay", other_team, "denied", "team_planning"),
        ("admin_demo", "people_admin", "read_pay", my_report, "allowed", "team_planning"),
    ]
    assert all(r[6] is not None for r in rows)  # "when"


def test_opening_a_record_needs_a_valid_reason(client, conn, my_report):
    assert get(client, f"/people/{my_report}/journey", "admin_demo", reason=None).status_code == 422
    assert (
        get(client, f"/people/{my_report}/pay", "admin_demo", reason="curious").status_code == 422
    )
    # Refused before any data is read, so there is nothing to audit.
    assert conn.execute("SELECT count(*) FROM audit_log").fetchone()[0] == 0


def test_employee_sees_who_opened_their_record(client, conn, other_team):
    assert get(client, f"/people/{EMPLOYEE}/journey", "manager_demo").status_code == 200
    assert (
        get(client, f"/people/{other_team}/journey", "admin_demo").status_code == 200
    )  # not theirs

    body = get(client, "/me/access-log", "employee_demo").json()
    assert body["person_id"] == EMPLOYEE
    assert [(a["username"], a["action"], a["outcome"], a["reason"]) for a in body["accesses"]] == [
        ("manager_demo", "read_journey", "allowed", "team_planning")
    ]
    # An employee cannot open anyone's record, including their own.
    assert get(client, f"/people/{EMPLOYEE}/journey", "employee_demo").status_code == 403


def test_account_without_a_person_has_no_access_log(client):
    assert get(client, "/me/access-log", "viewer_demo").status_code == 403


@pytest.mark.parametrize(
    "sql",
    ["UPDATE audit_log SET outcome = 'allowed'", "DELETE FROM audit_log", "TRUNCATE audit_log"],
)
def test_audit_log_cannot_be_changed_or_deleted(client, conn, sql):
    client.get("/insights/hiring-channels")  # a failed login, so there is a row to protect
    with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
        conn.execute(sql)
    conn.rollback()
    assert conn.execute("SELECT outcome FROM audit_log").fetchall() == [("denied",)]


def test_hiring_channel_insight(client):
    for user in TOKENS:
        assert get(client, "/insights/hiring-channels", user).status_code == 200
    body = get(client, "/insights/hiring-channels", "viewer_demo").json()
    assert "demo metric" in body["note"]
    assert {c["source_channel"] for c in body["channels"]} == {
        "referral", "job_board", "careers_site", "agency", "university",
    }  # fmt: skip


def login(client, username, password):
    return client.post("/login", json={"username": username, "password": password})


def test_front_end_is_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "People Records" in r.text


def test_login_checks_username_and_password_together(client, conn):
    ok = login(client, "Manager_Demo ", TOKENS["manager_demo"]).json()  # case and spaces forgiven
    assert ok["role"] == "manager" and ok["person_id"] == MANAGER and ok["name"]
    assert login(client, "admin_demo", TOKENS["admin_demo"]).json()["name"] is None

    assert login(client, "manager_demo", "wrong").status_code == 401
    assert login(client, "admin_demo", TOKENS["manager_demo"]).status_code == 401  # someone else's
    failed = conn.execute("SELECT count(*) FROM audit_log WHERE action = 'failed_login'")
    assert failed.fetchone()[0] == 2


def test_people_list_follows_the_access_rules(client, conn):
    everyone = get(client, "/people", "admin_demo").json()
    assert len(everyone) == conn.execute("SELECT count(*) FROM people").fetchone()[0]
    assert all("email" not in p for p in everyone)

    team = {p["person_id"] for p in get(client, "/people", "manager_demo").json()}
    reports = conn.execute("SELECT person_id FROM employees WHERE manager_id = %s", (MANAGER,))
    assert team == {r[0] for r in reports}

    assert get(client, "/people", "viewer_demo").status_code == 403
    assert get(client, "/people", "employee_demo").status_code == 403


def test_only_people_admin_reads_the_audit_log(client):
    get(client, f"/people/{EMPLOYEE}/journey", "manager_demo")
    rows = get(client, "/audit-log", "admin_demo").json()
    assert rows[0]["username"] == "manager_demo" and rows[0]["reason"] == "team_planning"
    for user in ("manager_demo", "viewer_demo", "employee_demo"):
        assert get(client, "/audit-log", user).status_code == 403


def test_demo_reset_needs_the_secret_and_reloads_everything(client, conn, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "vercel_entry", Path(__file__).parents[1] / "api" / "index.py"
    )
    spec.loader.exec_module(importlib.util.module_from_spec(spec))  # adds /reset-demo to the app

    assert client.get("/reset-demo").status_code == 401  # no secret configured
    monkeypatch.setenv("CRON_SECRET", "cron-test")
    for username, (_, var) in auth.DEMO_USERS.items():
        monkeypatch.setenv(var, TOKENS[username])
    assert client.get("/reset-demo", headers={"Authorization": "Bearer nope"}).status_code == 401

    get(client, f"/people/{EMPLOYEE}/journey", "manager_demo")  # a visitor's audit row
    r = client.get("/reset-demo", headers={"Authorization": "Bearer cron-test"})
    assert r.status_code == 200
    assert conn.execute("SELECT count(*) FROM people").fetchone()[0] == 150
    assert conn.execute("SELECT count(*) FROM audit_log").fetchone()[0] == 0
    assert get(client, "/insights/hiring-channels", "viewer_demo").status_code == 200
