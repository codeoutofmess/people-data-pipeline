"""Demo retention rule: blank the name and email of rejected candidates N days after the decision.

This is a demo rule for a learning project. It is NOT a legal or compliance claim, and real
retention periods depend on local law and company policy.

The application row stays (channel, stage, dates), so aggregate hiring statistics still work.
Only the personal details in `people` are removed.
"""

import logging
from datetime import date, timedelta

import psycopg

log = logging.getLogger(__name__)


def redact_rejected(
    conn: psycopg.Connection, days: int, dry_run: bool = False, today: date | None = None
) -> list[str]:
    """Returns the person_ids that were (or, in a dry run, would be) blanked."""
    cutoff = (today or date.today()) - timedelta(days=days)
    rows = conn.execute(
        """
        UPDATE people p
        SET full_name = NULL, email = NULL, redacted_at = now()
        FROM applications a
        WHERE a.person_id = p.person_id
          AND a.stage = 'rejected'
          AND a.decided_on < %s
          AND p.redacted_at IS NULL
        RETURNING p.person_id
        """,
        (cutoff,),
    ).fetchall()
    # A dry run makes the real change and then undoes it, so the count is exactly what a
    # real run would do.
    if dry_run:
        conn.rollback()
    else:
        conn.commit()
    ids = sorted(r[0] for r in rows)
    log.info(
        "retention finished",
        extra={"fields": {"days": days, "cutoff": cutoff, "dry_run": dry_run, "count": len(ids)}},
    )
    return ids
