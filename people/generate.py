"""Seeded generator for fake people data.

Same seed gives byte-identical files. Every name, email and amount is made up.
It writes four CSV files, then appends deliberately broken rows for the pipeline to catch.
"""

import csv
import random
from datetime import date, timedelta
from pathlib import Path

# A fixed "today", so the output never depends on the day you run it.
REFERENCE_DATE = date(2026, 6, 30)

FIRST_NAMES = [
    "Aroha", "Ben", "Chen", "Divya", "Elena", "Farid", "Grace", "Hemi", "Isla", "Jun",
    "Kavya", "Liam", "Mei", "Noah", "Olivia", "Priya", "Quinn", "Rangi", "Sofia", "Tane",
    "Uma", "Vikram", "Wiremu", "Xin", "Yusuf", "Zara", "Aiko", "Mateo", "Nia", "Omar",
]  # fmt: skip
LAST_NAMES = [
    "Anderson", "Bhat", "Clarke", "Dong", "Evans", "Fernandes", "Gupta", "Harris", "Ito",
    "Jones", "Kumar", "Lee", "Moana", "Ngata", "Okafor", "Patel", "Rossi", "Singh", "Tan",
    "Walker", "Williams", "Wong", "Young", "Zhang", "Silva", "Kaur", "Brown", "Nguyen",
]  # fmt: skip

CHANNELS = ["referral", "job_board", "careers_site", "agency", "university"]
TEAMS = ["Engineering", "Product", "Sales", "Operations", "People"]
ROLES = {
    "Engineering": ["Software Engineer", "Senior Software Engineer"],
    "Product": ["Product Manager", "Product Designer"],
    "Sales": ["Account Executive", "Sales Development Rep"],
    "Operations": ["Operations Analyst", "Office Coordinator"],
    "People": ["People Partner", "Recruiter"],
}
# Review cycle name -> last day of the cycle.
CYCLES = {
    "2024-H2": date(2024, 12, 31),
    "2025-H1": date(2025, 6, 30),
    "2025-H2": date(2025, 12, 31),
}
# Demo only: nudges review ratings up for some channels so the hiring-channel metric
# has a pattern to show. It is invented, not based on any real data.
CHANNEL_RATING_BONUS = {"referral": 0.5, "university": 0.2}
PAYROLL_FROM = date(2025, 7, 1)  # keep the file small: only the last 12 months of pay

APPLICATION_FIELDS = [
    "application_id", "person_id", "full_name", "email", "source_channel",
    "stage", "hired", "applied_on", "decided_on",
]  # fmt: skip
HR_FIELDS = ["person_id", "team", "role", "manager_id", "start_date", "status"]
PAYROLL_FIELDS = ["person_id", "period_start", "period_end", "gross_amount"]
REVIEW_FIELDS = ["review_id", "person_id", "cycle", "rating", "reviewer_id", "reviewed_on"]


def next_month(d: date) -> date:
    return date(d.year + d.month // 12, d.month % 12 + 1, 1)


def generate(out_dir: Path, seed: int = 42, n_people: int = 150) -> dict[str, int]:
    """Write the four source files into out_dir. Returns how many broken rows each file got."""
    rng = random.Random(seed)
    apps, hr, payroll, reviews = [], [], [], []
    team_manager = {}  # team -> manager's person_id

    for i in range(1, n_people + 1):
        pid = f"P{i:04d}"
        first, last = rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES)
        channel = rng.choice(CHANNELS)

        # Person 1 is the general manager and persons 2-6 lead one team each.
        # They were hired first, so everyone else has a manager in place.
        is_leader = i <= 1 + len(TEAMS)
        if is_leader:
            applied = REFERENCE_DATE - timedelta(days=rng.randint(1000, 1200))
            stage = "hired"
        else:
            applied = REFERENCE_DATE - timedelta(days=rng.randint(60, 900))
            stage = rng.choices(["hired", "rejected", "interview", "screening"], [35, 45, 10, 10])[
                0
            ]
        decided = (
            applied + timedelta(days=rng.randint(10, 50))
            if stage in ("hired", "rejected")
            else None
        )

        apps.append({
            "application_id": f"A{i:04d}", "person_id": pid,
            "full_name": f"{first} {last}", "email": f"{first}.{last}{i}@example.com".lower(),
            "source_channel": channel, "stage": stage, "hired": str(stage == "hired").lower(),
            "applied_on": applied, "decided_on": decided,
        })  # fmt: skip
        if stage != "hired":
            continue

        if i == 1:
            team, role, manager = "Leadership", "General Manager", None
        elif is_leader:
            team = TEAMS[i - 2]
            role, manager = f"{team} Lead", "P0001"
            team_manager[team] = pid
        else:
            team = rng.choice(TEAMS)
            role, manager = rng.choice(ROLES[team]), team_manager[team]
        start = decided + timedelta(days=rng.randint(14, 42))
        hr.append({
            "person_id": pid, "team": team, "role": role, "manager_id": manager,
            "start_date": start, "status": rng.choices(["active", "on_leave"], [95, 5])[0],
        })  # fmt: skip

        # Pay for each full month worked inside the payroll window.
        monthly = (rng.randrange(70_000, 150_000, 1_000) + (40_000 if is_leader else 0)) / 12
        month = max(next_month(start), PAYROLL_FROM)
        while month <= REFERENCE_DATE:
            payroll.append({
                "person_id": pid, "period_start": month,
                "period_end": next_month(month) - timedelta(days=1),
                "gross_amount": f"{monthly:.2f}",
            })  # fmt: skip
            month = next_month(month)

        # One review per cycle, if they had worked at least ~3 months by the end of it.
        if manager:
            for cycle, cycle_end in CYCLES.items():
                if start <= cycle_end - timedelta(days=90):
                    mean = 3.2 + CHANNEL_RATING_BONUS.get(channel, 0)
                    reviews.append({
                        "review_id": f"R{len(reviews) + 1:05d}", "person_id": pid,
                        "cycle": cycle, "rating": min(5, max(1, round(rng.gauss(mean, 0.8)))),
                        "reviewer_id": manager,
                        "reviewed_on": cycle_end + timedelta(days=rng.randint(5, 25)),
                    })  # fmt: skip

    broken = add_broken_rows(apps, hr, payroll, reviews)

    out_dir.mkdir(parents=True, exist_ok=True)
    write_csv(out_dir / "applications.csv", APPLICATION_FIELDS, apps)
    write_csv(out_dir / "hr_records.csv", HR_FIELDS, hr)
    write_csv(out_dir / "payroll.csv", PAYROLL_FIELDS, payroll)
    write_csv(out_dir / "reviews.csv", REVIEW_FIELDS, reviews)
    return broken


def add_broken_rows(apps, hr, payroll, reviews) -> dict[str, int]:
    """Append rows the pipeline must quarantine. Each comment names the problem.

    Some rows have two problems (for example a bad date on a person who was never hired).
    The pipeline reports the first check that fails.
    """
    rejected = [a["person_id"] for a in apps if a["stage"] == "rejected"]
    # New applicants P9001-P9004, each copied from a clean row with one field broken.
    new_ids = [{"application_id": f"A900{n}", "person_id": f"P900{n}"} for n in range(1, 5)]
    new_apps = [
        dict(apps[10]),  # exact duplicate of an earlier row
        {**apps[11], **new_ids[0], "email": ""},  # missing email
        {**apps[12], **new_ids[1], "applied_on": "2025-13-45"},  # bad date
        {**apps[13], **new_ids[2], "source_channel": "fax"},  # unknown channel
        {**apps[14], **new_ids[3], "stage": "rejected", "hired": "true"},  # hired contradicts stage
    ]
    new_hr = [
        dict(hr[10]),  # duplicate person_id
        {**hr[11], "person_id": "P8888"},  # person not in applications
        {**hr[12], "person_id": rejected[0], "start_date": "31/02/2025"},  # bad date
        {**hr[13], "person_id": rejected[1], "team": ""},  # missing team
        {**hr[14], "person_id": rejected[2]},  # HR record for someone who was not hired
    ]
    new_payroll = [
        dict(payroll[20]),  # duplicate person + period
        {**payroll[21], "gross_amount": "-500.00"},  # negative pay
        {**payroll[22], "person_id": "P8888"},  # unknown person
        {**payroll[23], "gross_amount": ""},  # missing amount
        {**payroll[24], "period_start": "2025-02-30"},  # bad date
    ]
    new_reviews = [
        {**reviews[5], "review_id": "R90001", "rating": "7"},  # rating out of range
        {**reviews[6], "review_id": "R90002", "person_id": "P8888"},  # unknown person
        {**reviews[7], "review_id": "R90003", "reviewed_on": "not a date"},  # bad date
        dict(reviews[8]),  # duplicate review_id
        {**reviews[9], "review_id": "R90004", "reviewer_id": "P7777"},  # unknown reviewer
    ]
    apps += new_apps
    hr += new_hr
    payroll += new_payroll
    reviews += new_reviews
    return {
        "applications": len(new_apps),
        "hr_records": len(new_hr),
        "payroll": len(new_payroll),
        "reviews": len(new_reviews),
    }


def write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    # Fixed "\n" line endings keep the bytes identical on every operating system.
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
