import os

import psycopg
import pytest

from people import db
from people.generate import generate

# A separate database, because every test wipes it.
TEST_URL = os.environ.get("TEST_DATABASE_URL", "postgresql:///people_test")


@pytest.fixture
def conn():
    """A connection to an empty test database with all tables created."""
    with psycopg.connect(TEST_URL) as c:
        c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        db.init_db(c)
        yield c


@pytest.fixture
def raw_dir(tmp_path):
    """The four generated CSV files (seed 42), each with 5 broken rows."""
    broken = generate(tmp_path, seed=42)
    assert all(n == 5 for n in broken.values())
    return tmp_path
