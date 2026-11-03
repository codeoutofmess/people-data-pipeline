# people-data-pipeline

A learning project: a small "people data" backend built on **synthetic data only**.
Fake hiring, HR, payroll and performance-review files go through a tested Python pipeline
into one Postgres database, with a small access-controlled API on top.

It is a generic interpretation for practice, not any real company's system, and it has never
handled real people's data.

> Work in progress. Milestones 1 to 3 (data, pipeline, API) are done.
> Full documentation comes in Milestone 4.

## Run it locally

Needs Python 3.12+, [uv](https://docs.astral.sh/uv/) and a local Postgres (for example
`brew install postgresql@17`).

```bash
createdb people && createdb people_test
uv sync
uv run people init-db      # create the tables
uv run people generate     # write data/raw/*.csv (seed 42; --seed to change)
uv run people run          # validate, quarantine bad rows, load good rows, print the summary
uv run people report       # print the latest run's data-quality summary again
uv run pytest
```

Connection settings come from `DATABASE_URL` and `TEST_DATABASE_URL` (see `.env.example`).
To load them from a `.env` file, use `uv run --env-file .env ...`.

## The data

Four CSV files, linked by one `person_id`:

| File | What it holds |
|---|---|
| `applications.csv` | Every candidate: name, email, source channel, stage, hired flag, dates |
| `hr_records.csv` | Hired people only: team, role, manager, start date, status |
| `payroll.csv` | Monthly pay periods with made-up amounts |
| `reviews.csv` | Performance reviews: cycle, rating 1-5, reviewer, date |

The generator is deterministic: the same seed gives byte-identical files. It then appends
5 deliberately broken rows to each file (duplicates, missing fields, bad dates, IDs that do not
exist elsewhere, out-of-range values) for the pipeline's validation to catch.

The generator also gives referral and university hires a small, invented boost to their review
ratings. With the default 150 people the samples are so small that random noise hides it (at
seed 42, referral actually ranks fourth). Generate a few thousand people and it shows clearly.
That is exactly why the API labels the metric as a simple demo with small samples.

## The pipeline

`people run` reads the four files in dependency order (applications, then HR records, then
payroll and reviews) and, for each row:

1. **Validates its fields** with Pydantic: required fields, types, dates, allowed values, ranges.
2. **Drops duplicates** inside the file (the first copy wins).
3. **Checks links** to the other files: an HR record must belong to a hired applicant, pay and
   reviews must belong to an employee, managers and reviewers must exist.

A row that fails any check goes to the `quarantine` table with the first reason it failed.
Good rows are **upserted** (`INSERT ... ON CONFLICT DO UPDATE`), so running the same files again
leaves the data unchanged. The whole run is one database transaction: it lands completely or not
at all. Each run stores a summary (rows in, loaded, quarantined, reasons) in `pipeline_runs`,
which `people report` prints. Logs are written to stderr as one JSON object per line.

## The API (demo authentication)

```bash
cp .env.example .env       # then put three long random tokens in it
uv run --env-file .env people seed-users          # creates admin_demo, manager_demo, viewer_demo
uv run --env-file .env uvicorn people.api:app      # http://localhost:8000/docs
curl -H "Authorization: Bearer $DEMO_TOKEN_MANAGER" localhost:8000/people/P0009/journey
```

Authentication is **demo only**: three seeded users, each with a static bearer token from an
environment variable. Only a SHA-256 hash of each token is stored. There is no login, no expiry
and no identity provider.

| Endpoint | people_admin | manager | viewer |
|---|---|---|---|
| `GET /people/{id}/journey` (application, hire, pay, reviews in date order) | anyone, with pay | direct reports only, without pay | 403 |
| `GET /people/{id}/pay` | yes | 403 | 403 |
| `GET /insights/hiring-channels` (aggregate, demo metric) | yes | yes | yes |

Every read of person data, allowed or denied, writes a row to `audit_log` (who, role, what,
which person, when, outcome). A manager asking for a person who is not their report gets 403
even if the person does not exist, so the API does not reveal who is in the system.

## Retention (demo rule)

```bash
uv run people retention --days 180 --dry-run   # how many would be affected
uv run people retention --days 180             # blank them
```

Blanks the name and email of rejected candidates whose decision is more than N days old, and
records when. The application row stays, so hiring-channel statistics still work, and later
pipeline runs never refill blanked details. **This is a demo rule for a learning project, not a
legal or compliance claim.** Real retention periods depend on law and company policy.
