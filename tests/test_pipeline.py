import psycopg
import pytest

from people import cli, pipeline

from .conftest import TEST_URL

DATA_TABLES = ["people", "applications", "employees", "pay_records", "reviews"]


def table_counts(conn) -> dict[str, int]:
    return {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in DATA_TABLES}


def test_every_broken_row_is_quarantined_and_nothing_else(conn, raw_dir):
    summary = pipeline.run(conn, raw_dir)
    for source, s in summary["sources"].items():
        assert s["quarantined"] == 5, source
        assert s["loaded"] == s["rows_in"] - 5, source


def test_quarantine_reasons(conn, raw_dir):
    pipeline.run(conn, raw_dir)
    rows = conn.execute("SELECT source, raw, reason FROM quarantine").fetchall()
    reasons = {(source, raw.get("person_id"), raw.get("review_id")): r for source, raw, r in rows}

    assert all(reason for *_, reason in rows), "every quarantined row needs a reason"
    assert not any({"full_name", "email"} & raw.keys() for _, raw, _ in rows), "no personal data"
    assert reasons[("applications", "P9001", None)].startswith("email:")
    assert reasons[("applications", "P9002", None)].startswith("applied_on:")
    assert reasons[("applications", "P9003", None)].startswith("source_channel:")
    assert "hired flag contradicts stage" in reasons[("applications", "P9004", None)]
    assert reasons[("hr_records", "P8888", None)] == "person_id not found in applications"
    assert reasons[("payroll", "P8888", None)] == "person_id not found in HR records"
    assert reasons[("reviews", "P0003", "R90001")].startswith("rating:")
    assert reasons[("reviews", "P0005", "R90004")] == "reviewer_id not found in HR records"
    duplicates = [r for *_, r in rows if r == "duplicate of an earlier row in the file"]
    assert len(duplicates) == 4  # one per file


def test_rerun_does_not_duplicate_data(conn, raw_dir):
    pipeline.run(conn, raw_dir)
    first = table_counts(conn)
    pipeline.run(conn, raw_dir)
    assert table_counts(conn) == first
    # Quarantine is a per-run log, so each run records its own 20 rows.
    per_run = conn.execute("SELECT run_id, count(*) FROM quarantine GROUP BY run_id").fetchall()
    assert [n for _, n in per_run] == [20, 20]


def test_rerun_picks_up_a_corrected_value(conn, raw_dir):
    pipeline.run(conn, raw_dir)
    path = raw_dir / "hr_records.csv"
    path.write_text(
        path.read_text().replace(
            "P0002,Engineering,Engineering Lead", "P0002,Engineering,Head of Engineering", 1
        )
    )
    pipeline.run(conn, raw_dir)
    role = conn.execute("SELECT role FROM employees WHERE person_id = 'P0002'").fetchone()[0]
    assert role == "Head of Engineering"


def test_failed_run_leaves_no_half_loaded_data(conn, raw_dir, monkeypatch):
    pipeline.run(conn, raw_dir)
    before = table_counts(conn)

    # Change a value in the source, then make the LAST load step crash. By then the earlier
    # upserts (people, applications, employees, pay) have already run inside the transaction.
    path = raw_dir / "hr_records.csv"
    path.write_text(path.read_text().replace("Engineering Lead", "Head of Engineering", 1))
    monkeypatch.setattr(pipeline, "UPSERT_REVIEW", "INSERT INTO no_such_table VALUES (1)")

    # Same pattern the CLI uses: leaving the `with` block on an error rolls the transaction back.
    with pytest.raises(psycopg.errors.UndefinedTable):
        with psycopg.connect(TEST_URL) as other:
            pipeline.run(other, raw_dir)

    assert table_counts(conn) == before
    role = conn.execute("SELECT role FROM employees WHERE person_id = 'P0002'").fetchone()[0]
    assert role == "Engineering Lead", "the change from the failed run must not be saved"
    assert conn.execute("SELECT count(*) FROM pipeline_runs").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM quarantine").fetchone()[0] == 20


def test_run_and_report_commands(conn, raw_dir, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", TEST_URL)
    cli.main(["report"])
    assert "No pipeline runs yet" in capsys.readouterr().out

    cli.main(["run", "--raw", str(raw_dir)])
    cli.main(["report"])
    out = capsys.readouterr().out
    assert "Pipeline run 1" in out
    assert "line" in out and "rating:" in out
