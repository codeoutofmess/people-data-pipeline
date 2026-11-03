"""The API. Run it with: uv run uvicorn people.api:app --reload

Access rules (demo):
- people_admin: every person, including pay.
- manager: only their direct reports, never pay.
- viewer: no person data, only the aggregate hiring-channel insight.
Every read of person data, allowed or denied, is written to audit_log.
"""

import psycopg
from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from psycopg.rows import dict_row

from people import db
from people.auth import User, find_user

app = FastAPI(
    title="People data demo API",
    description="Learning project on synthetic data only. Demo authentication, not production.",
)
bearer = HTTPBearer(auto_error=False)  # we raise our own 401 below


def get_conn():
    # shortcut: a new connection per request. Add a connection pool (psycopg_pool) if traffic grows.
    with db.connect() as conn:
        conn.autocommit = True  # each statement saves immediately, so audit rows always stick
        conn.row_factory = dict_row  # rows come back as dicts, ready to return as JSON
        yield conn


def current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    conn: psycopg.Connection = Depends(get_conn),
) -> User:
    user = find_user(conn, creds.credentials) if creds else None
    if not user:
        raise HTTPException(401, "Missing or invalid token", {"WWW-Authenticate": "Bearer"})
    return user


def can_read_person(conn: psycopg.Connection, user: User, person_id: str) -> bool:
    if user.role == "people_admin":
        return True
    if user.role == "manager":
        return bool(
            conn.execute(
                "SELECT 1 FROM employees WHERE person_id = %s AND manager_id = %s",
                (person_id, user.person_id),
            ).fetchone()
        )
    return False


def audit(conn: psycopg.Connection, user: User, action: str, person_id: str, allowed: bool):
    conn.execute(
        "INSERT INTO audit_log (username, role, action, person_id, outcome)"
        " VALUES (%s, %s, %s, %s, %s)",
        (user.username, user.role, action, person_id, "allowed" if allowed else "denied"),
    )


def check_access(conn, user: User, action: str, person_id: str, allowed: bool) -> None:
    """Audit first, then refuse. A denied attempt is logged too, and the answer is the same
    403 whether or not the person exists, so it does not reveal who is in the system."""
    audit(conn, user, action, person_id, allowed)
    if not allowed:
        raise HTTPException(403, "You are not allowed to read this data")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/people/{person_id}/journey")
def journey(
    person_id: str,
    user: User = Depends(current_user),
    conn: psycopg.Connection = Depends(get_conn),
):
    """One person's events in date order: application, decision, start, pay, reviews.
    Pay events are included only for people_admin."""
    check_access(conn, user, "read_journey", person_id, can_read_person(conn, user, person_id))

    person = conn.execute(
        "SELECT person_id, full_name, email, redacted_at FROM people WHERE person_id = %s",
        (person_id,),
    ).fetchone()
    if not person:
        raise HTTPException(404, "Person not found")

    events = []
    for a in conn.execute("SELECT * FROM applications WHERE person_id = %s", (person_id,)):
        events.append({"date": a["applied_on"], "event": "applied", "channel": a["source_channel"]})
        if a["decided_on"]:
            events.append({"date": a["decided_on"], "event": a["stage"]})  # hired or rejected
    for e in conn.execute("SELECT * FROM employees WHERE person_id = %s", (person_id,)):
        events.append(
            {"date": e["start_date"], "event": "started", "team": e["team"], "role": e["role"],
             "manager_id": e["manager_id"]}
        )  # fmt: skip
    if user.role == "people_admin":
        for p in conn.execute("SELECT * FROM pay_records WHERE person_id = %s", (person_id,)):
            events.append(
                {"date": p["period_end"], "event": "paid", "period_start": p["period_start"],
                 "gross_amount": p["gross_amount"]}
            )  # fmt: skip
    for r in conn.execute("SELECT * FROM reviews WHERE person_id = %s", (person_id,)):
        events.append(
            {"date": r["reviewed_on"], "event": "reviewed", "cycle": r["cycle"],
             "rating": r["rating"], "reviewer_id": r["reviewer_id"]}
        )  # fmt: skip

    events.sort(key=lambda ev: ev["date"])
    return {**person, "events": events}


@app.get("/people/{person_id}/pay")
def pay(
    person_id: str,
    user: User = Depends(current_user),
    conn: psycopg.Connection = Depends(get_conn),
):
    """Pay history. people_admin only."""
    check_access(conn, user, "read_pay", person_id, user.role == "people_admin")
    rows = conn.execute(
        "SELECT period_start, period_end, gross_amount FROM pay_records"
        " WHERE person_id = %s ORDER BY period_start",
        (person_id,),
    ).fetchall()
    return {"person_id": person_id, "pay": rows}


@app.get("/insights/hiring-channels")
def hiring_channels(
    user: User = Depends(current_user),
    conn: psycopg.Connection = Depends(get_conn),
):
    """Average later review rating of hires, per source channel. Aggregate only, no
    individual's data, so every role may see it and it is not audited."""
    rows = conn.execute(
        """
        SELECT a.source_channel,
               count(DISTINCT a.person_id)                    AS applicants,
               count(DISTINCT a.person_id) FILTER (WHERE a.hired) AS hires,
               count(DISTINCT r.person_id)                    AS reviewed_hires,
               count(r.review_id)                             AS reviews,
               round(avg(r.rating), 2)                        AS avg_review_rating
        FROM applications a
        LEFT JOIN reviews r ON r.person_id = a.person_id
        GROUP BY a.source_channel
        ORDER BY avg_review_rating DESC NULLS LAST
        """
    ).fetchall()
    return {
        "metric": "average performance-review rating of hires, by the channel they applied through",
        "note": (
            "Simple demo metric on synthetic data. Samples are small, nothing is adjusted for "
            "team, role or tenure, and it does not show that a channel causes better performance."
        ),
        "channels": rows,
    }
