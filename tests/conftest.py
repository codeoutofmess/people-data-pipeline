import os

import psycopg
import pytest

from people import db

# A separate database, because every test wipes it.
TEST_URL = os.environ.get("TEST_DATABASE_URL", "postgresql:///people_test")


@pytest.fixture
def conn():
    """A connection to an empty test database with all tables created."""
    with psycopg.connect(TEST_URL) as c:
        c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        db.init_db(c)
        yield c
