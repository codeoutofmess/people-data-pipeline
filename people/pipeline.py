"""The pipeline: read each CSV, validate every row, quarantine bad rows with a reason,
and upsert good rows into Postgres. Each run stores a data-quality summary.

Each run treats the four files as a full snapshot of the source systems.
shortcut: rows that disappear from a file are not deleted from the database.
Add a "soft delete" pass if sources start removing rows.
"""

import csv
import logging
from collections import Counter
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ValidationError

from people.models import Application, Employee, PayRecord, Review, describe

log = logging.getLogger(__name__)

# (row_number, raw row as read, validated record)
Good = tuple[int, dict, BaseModel]
# (row_number, raw row as read, reason)
Bad = tuple[int, dict, str]


def read_and_validate(path: Path, model: type[BaseModel], key) -> tuple[list[Good], list[Bad]]:
    """Pass 1: check each row's own fields, and drop in-file duplicates (first copy wins)."""
    good, bad, seen = [], [], set()
    with path.open(newline="") as f:
        for row_number, raw in enumerate(csv.DictReader(f), start=2):  # line 1 is the header
            try:
                record = model.model_validate(raw)
            except ValidationError as e:
                bad.append((row_number, raw, describe(e)))
                continue
            if key(record) in seen:
                bad.append((row_number, raw, "duplicate of an earlier row in the file"))
                continue
            seen.add(key(record))
            good.append((row_number, raw, record))
    return good, bad


def keep_valid_links(good: list[Good], bad: list[Bad], problem) -> list[Good]:
    """Pass 2: cross-file checks. `problem(record)` returns a reason, or None if the row is fine.
    Failing rows move to `bad`; the rest are returned."""
    kept = []
    for row_number, raw, record in good:
        reason = problem(record)
        if reason:
            bad.append((row_number, raw, reason))
        else:
            kept.append((row_number, raw, record))
    return kept


def validate_all(raw_dir: Path) -> dict[str, tuple[list[Good], list[Bad]]]:
    """Validate the four files in dependency order: later files point at people in earlier ones."""
    apps, apps_bad = read_and_validate(
        raw_dir / "applications.csv", Application, lambda r: r.application_id
    )
    applicants = {r.person_id for _, _, r in apps}
    hired = {r.person_id for _, _, r in apps if r.hired}

    def employee_problem(r: Employee) -> str | None:
        if r.person_id not in applicants:
            return "person_id not found in applications"
        if r.person_id not in hired:
            return "person was not hired"
        return None

    def manager_problem(r: Employee) -> str | None:
        if r.manager_id and r.manager_id not in employee_ids:
            return "manager_id not found in HR records"
        return None

    def review_problem(r: Review) -> str | None:
        if r.person_id not in employee_ids:
            return "person_id not found in HR records"
        if r.reviewer_id not in employee_ids:
            return "reviewer_id not found in HR records"
        return None

    emps, emps_bad = read_and_validate(raw_dir / "hr_records.csv", Employee, lambda r: r.person_id)
    emps = keep_valid_links(emps, emps_bad, employee_problem)
    # shortcut: one pass. If a manager's own manager were quarantined, the database's foreign key
    # would fail the whole run at commit (nothing half-loaded). Loop this check if that happens.
    employee_ids = {r.person_id for _, _, r in emps}
    emps = keep_valid_links(emps, emps_bad, manager_problem)
    employee_ids = {r.person_id for _, _, r in emps}

    pay, pay_bad = read_and_validate(
        raw_dir / "payroll.csv", PayRecord, lambda r: (r.person_id, r.period_start)
    )
    pay = keep_valid_links(
        pay,
        pay_bad,
        lambda r: None if r.person_id in employee_ids else "person_id not found in HR records",
    )

    revs, revs_bad = read_and_validate(raw_dir / "reviews.csv", Review, lambda r: r.review_id)
    revs = keep_valid_links(revs, revs_bad, review_problem)

    return {
        "applications": (apps, apps_bad),
        "hr_records": (emps, emps_bad),
        "payroll": (pay, pay_bad),
        "reviews": (revs, revs_bad),
    }


# Upserts: insert the row, or update it if the key already exists. Running the same files
# twice therefore leaves the database exactly as it was after the first run.
UPSERT_PERSON = """
    INSERT INTO people (person_id, full_name, email)
    VALUES (%(person_id)s, %(full_name)s, %(email)s)
    ON CONFLICT (person_id) DO UPDATE
        SET full_name = EXCLUDED.full_name, email = EXCLUDED.email
        -- Never refill a name or email that the retention command has blanked.
        WHERE people.redacted_at IS NULL
"""
UPSERT_APPLICATION = """
    INSERT INTO applications
        (application_id, person_id, source_channel, stage, hired, applied_on, decided_on)
    VALUES (%(application_id)s, %(person_id)s, %(source_channel)s, %(stage)s, %(hired)s,
            %(applied_on)s, %(decided_on)s)
    ON CONFLICT (application_id) DO UPDATE
        SET person_id = EXCLUDED.person_id, source_channel = EXCLUDED.source_channel,
            stage = EXCLUDED.stage, hired = EXCLUDED.hired,
            applied_on = EXCLUDED.applied_on, decided_on = EXCLUDED.decided_on
"""
UPSERT_EMPLOYEE = """
    INSERT INTO employees (person_id, team, role, manager_id, start_date, status)
    VALUES (%(person_id)s, %(team)s, %(role)s, %(manager_id)s, %(start_date)s, %(status)s)
    ON CONFLICT (person_id) DO UPDATE
        SET team = EXCLUDED.team, role = EXCLUDED.role, manager_id = EXCLUDED.manager_id,
            start_date = EXCLUDED.start_date, status = EXCLUDED.status
"""
UPSERT_PAY = """
    INSERT INTO pay_records (person_id, period_start, period_end, gross_amount)
    VALUES (%(person_id)s, %(period_start)s, %(period_end)s, %(gross_amount)s)
    ON CONFLICT (person_id, period_start) DO UPDATE
        SET period_end = EXCLUDED.period_end, gross_amount = EXCLUDED.gross_amount
"""
UPSERT_REVIEW = """
    INSERT INTO reviews (review_id, person_id, cycle, rating, reviewer_id, reviewed_on)
    VALUES (%(review_id)s, %(person_id)s, %(cycle)s, %(rating)s, %(reviewer_id)s,
            %(reviewed_on)s)
    ON CONFLICT (review_id) DO UPDATE
        SET person_id = EXCLUDED.person_id, cycle = EXCLUDED.cycle, rating = EXCLUDED.rating,
            reviewer_id = EXCLUDED.reviewer_id, reviewed_on = EXCLUDED.reviewed_on
"""


def run(conn: psycopg.Connection, raw_dir: Path) -> dict:
    """Validate and load everything in ONE transaction: either the whole run lands or none of it."""
    run_id = conn.execute("INSERT INTO pipeline_runs DEFAULT VALUES RETURNING run_id").fetchone()[0]
    log.info("run started", extra={"fields": {"run_id": run_id, "raw_dir": str(raw_dir)}})

    results = validate_all(raw_dir)

    def records(source: str) -> list[dict]:
        return [record.model_dump() for _, _, record in results[source][0]]

    with conn.cursor() as cur:
        cur.executemany(UPSERT_PERSON, records("applications"))
        cur.executemany(UPSERT_APPLICATION, records("applications"))
        cur.executemany(UPSERT_EMPLOYEE, records("hr_records"))
        cur.executemany(UPSERT_PAY, records("payroll"))
        cur.executemany(UPSERT_REVIEW, records("reviews"))

        summary = {"run_id": run_id, "sources": {}}
        for source, (good, bad) in results.items():
            cur.executemany(
                "INSERT INTO quarantine (run_id, source, row_number, raw, reason)"
                " VALUES (%s, %s, %s, %s, %s)",
                [(run_id, source, n, Jsonb(raw), reason) for n, raw, reason in bad],
            )
            for n, _, reason in bad:
                log.warning(
                    "row quarantined",
                    extra={"fields": {"source": source, "row_number": n, "reason": reason}},
                )
            summary["sources"][source] = {
                "rows_in": len(good) + len(bad),
                "loaded": len(good),
                "quarantined": len(bad),
                "reasons": dict(Counter(reason for _, _, reason in bad)),
            }

        cur.execute(
            "UPDATE pipeline_runs SET finished_at = now(), summary = %s WHERE run_id = %s",
            (Jsonb(summary), run_id),
        )
    conn.commit()
    log.info("run finished", extra={"fields": summary})
    return summary


SOURCES = ["applications", "hr_records", "payroll", "reviews"]


def report(conn: psycopg.Connection) -> str:
    """The latest finished run's data-quality summary, as printable text."""
    row = conn.execute(
        "SELECT run_id, finished_at, summary FROM pipeline_runs"
        " WHERE finished_at IS NOT NULL ORDER BY run_id DESC LIMIT 1"
    ).fetchone()
    if not row:
        return "No pipeline runs yet. Run `people run` first."
    run_id, finished_at, summary = row

    lines = [
        f"Pipeline run {run_id}, finished {finished_at:%Y-%m-%d %H:%M:%S}",
        "",
        f"{'source':<14}{'rows in':>9}{'loaded':>9}{'quarantined':>13}",
    ]
    # Postgres stores JSON keys in its own order, so loop over a fixed list instead.
    for source in SOURCES:
        s = summary["sources"][source]
        lines.append(f"{source:<14}{s['rows_in']:>9}{s['loaded']:>9}{s['quarantined']:>13}")

    lines += ["", "Quarantined rows (line numbers count the header as line 1):"]
    quarantined = conn.execute(
        "SELECT source, row_number, reason FROM quarantine WHERE run_id = %s", (run_id,)
    ).fetchall()
    quarantined.sort(key=lambda q: (SOURCES.index(q[0]), q[1]))  # file order, then line order
    for source, row_number, reason in quarantined:
        lines.append(f"  {source:<14} line {row_number:<5} {reason}")
    return "\n".join(lines)
