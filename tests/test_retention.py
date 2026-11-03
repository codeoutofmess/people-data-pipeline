from datetime import timedelta

from people import pipeline, retention
from people.generate import REFERENCE_DATE

DAYS = 180
CUTOFF = REFERENCE_DATE - timedelta(days=DAYS)


def person(conn, person_id):
    return conn.execute(
        "SELECT full_name, email, redacted_at FROM people WHERE person_id = %s", (person_id,)
    ).fetchone()


def test_retention_blanks_name_and_email_and_rerun_does_not_refill(conn, raw_dir):
    pipeline.run(conn, raw_dir)
    blanked = retention.redact_rejected(conn, DAYS, today=REFERENCE_DATE)
    assert blanked
    target = blanked[0]

    full_name, email, redacted_at = person(conn, target)
    assert (full_name, email) == (None, None) and redacted_at is not None

    # The source file still has this person's name and email. A new run must not restore them.
    pipeline.run(conn, raw_dir)
    assert person(conn, target)[:2] == (None, None)

    # The application row is kept, so channel statistics still count this candidate.
    stage = conn.execute("SELECT stage FROM applications WHERE person_id = %s", (target,))
    assert stage.fetchone()[0] == "rejected"


def test_retention_only_touches_old_rejections(conn, raw_dir):
    pipeline.run(conn, raw_dir)
    blanked = set(retention.redact_rejected(conn, DAYS, today=REFERENCE_DATE))
    rows = conn.execute(
        "SELECT a.person_id, a.stage, a.decided_on, p.full_name"
        " FROM applications a JOIN people p USING (person_id)"
    ).fetchall()
    for person_id, stage, decided_on, full_name in rows:
        should_blank = stage == "rejected" and decided_on < CUTOFF
        assert (person_id in blanked) == should_blank, person_id
        assert (full_name is None) == should_blank, person_id
    assert any(s == "rejected" and d >= CUTOFF for _, s, d, _ in rows), "test needs a recent one"


def test_dry_run_changes_nothing(conn, raw_dir):
    pipeline.run(conn, raw_dir)
    would = retention.redact_rejected(conn, DAYS, dry_run=True, today=REFERENCE_DATE)
    assert would
    assert conn.execute("SELECT count(*) FROM people WHERE full_name IS NULL").fetchone()[0] == 0
    # ...and a real run then blanks exactly the people the dry run listed.
    assert retention.redact_rejected(conn, DAYS, today=REFERENCE_DATE) == would
