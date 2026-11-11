"""Demo authentication: three seeded users, each with a bearer token.

This is NOT real authentication. There is no login and no identity provider.
A client sends `Authorization: Bearer <token>`. We hash the token and look up the user,
and an expired token is treated like a wrong one.
Only the SHA-256 hash is stored, so reading the database does not reveal anyone's token.
"""

import hashlib
import os
from dataclasses import dataclass

import psycopg

# username -> (role, environment variable holding that user's token)
DEMO_USERS = {
    "admin_demo": ("people_admin", "DEMO_TOKEN_PEOPLE_ADMIN"),
    "manager_demo": ("manager", "DEMO_TOKEN_MANAGER"),
    "viewer_demo": ("viewer", "DEMO_TOKEN_VIEWER"),
}


@dataclass
class User:
    username: str
    role: str
    person_id: str | None  # for a manager: whose direct reports they can see


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def seed_users(
    conn: psycopg.Connection, tokens: dict[str, str], manager_person_id: str, days: int = 30
) -> None:
    """Create or replace the three demo users. `tokens` maps username -> token.
    Each token stops working `days` days from now; run this again with new tokens to rotate."""
    for username, (role, _) in DEMO_USERS.items():
        conn.execute(
            """
            INSERT INTO api_users (username, role, token_sha256, person_id, expires_at)
            VALUES (%s, %s, %s, %s, now() + make_interval(days => %s))
            ON CONFLICT (username) DO UPDATE
                SET role = EXCLUDED.role, token_sha256 = EXCLUDED.token_sha256,
                    person_id = EXCLUDED.person_id, expires_at = EXCLUDED.expires_at
            """,
            (
                username,
                role,
                hash_token(tokens[username]),
                manager_person_id if role == "manager" else None,
                days,
            ),
        )
    conn.commit()


def tokens_from_env() -> dict[str, str]:
    missing = [var for _, var in DEMO_USERS.values() if not os.environ.get(var)]
    if missing:
        raise SystemExit(f"Set these environment variables first (see .env.example): {missing}")
    return {username: os.environ[var] for username, (_, var) in DEMO_USERS.items()}


def find_user(conn: psycopg.Connection, token: str) -> User | None:
    row = conn.execute(
        "SELECT username, role, person_id FROM api_users"
        " WHERE token_sha256 = %s AND expires_at > now()",
        (hash_token(token),),
    ).fetchone()
    return User(**row) if row else None
