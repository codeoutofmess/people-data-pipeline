import csv

from people import db
from people.generate import generate

FILES = ["applications.csv", "hr_records.csv", "payroll.csv", "reviews.csv"]


def test_same_seed_gives_identical_files(tmp_path):
    generate(tmp_path / "a", seed=7)
    generate(tmp_path / "b", seed=7)
    for name in FILES:
        assert (tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes()


def test_different_seed_gives_different_data(tmp_path):
    generate(tmp_path / "a", seed=7)
    generate(tmp_path / "b", seed=8)
    a = (tmp_path / "a" / "applications.csv").read_bytes()
    assert a != (tmp_path / "b" / "applications.csv").read_bytes()


def test_broken_rows_are_appended(tmp_path):
    broken = generate(tmp_path, seed=7)
    assert broken == {"applications": 5, "hr_records": 5, "payroll": 5, "reviews": 5}
    rows = list(csv.DictReader((tmp_path / "reviews.csv").open()))
    assert any(r["rating"] == "7" for r in rows)


def test_clean_rows_link_by_person_id(tmp_path):
    generate(tmp_path, seed=7)
    apps = csv.DictReader((tmp_path / "applications.csv").open())
    hired = {a["person_id"] for a in apps if a["hired"] == "true"}
    # Every HR row except the 5 broken ones at the end belongs to a hired applicant.
    hr = list(csv.DictReader((tmp_path / "hr_records.csv").open()))[:-5]
    assert hr and all(r["person_id"] in hired for r in hr)


def test_schema_applies_twice_without_error(conn):
    db.init_db(conn)  # the fixture already ran it once
    tables = conn.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'"
    ).fetchone()[0]
    assert tables == 9
