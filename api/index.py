"""Vercel entry point for the live demo. Locally, run `uvicorn people.api:app` instead.

It adds one route that only the live demo needs: a nightly reset, called by Vercel Cron
(see vercel.json). The demo logins are public, so the reset puts back the same fake data and
clears whatever visitors did, including their audit-log rows.
"""

import hmac
import os
import tempfile
from pathlib import Path

from fastapi import Header, HTTPException

from people import auth, db, generate, pipeline
from people.api import app


@app.get("/reset-demo", include_in_schema=False)
def reset_demo(authorization: str = Header("")):
    # Vercel Cron sends "Bearer <CRON_SECRET>". Without the secret set, the route is disabled.
    secret = os.environ.get("CRON_SECRET", "")
    if not secret or not hmac.compare_digest(authorization.encode(), f"Bearer {secret}".encode()):
        raise HTTPException(401, "Not allowed")
    with tempfile.TemporaryDirectory() as tmp, db.connect() as conn:
        db.init_db(conn, reset=True)
        generate.generate(Path(tmp))
        summary = pipeline.run(conn, Path(tmp))
        auth.seed_users(conn, auth.tokens_from_env(), manager_person_id="P0002",
                        employee_person_id="P0009")  # fmt: skip
    return {"status": "reset", "run_id": summary["run_id"]}
