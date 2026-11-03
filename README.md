# people-data-pipeline

A learning project: a small "people data" backend built on **synthetic data only**.
Fake hiring, HR, payroll and performance-review files go through a tested Python pipeline
into one Postgres database, with a small access-controlled API on top.

It is a generic interpretation for practice, not any real company's system, and it has never
handled real people's data.

> Work in progress. Milestones 1 (data model, synthetic data) and 2 (pipeline) are done.
> The API and full documentation come in later milestones.

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

The generator also deliberately gives referral and university hires slightly higher review
ratings, so the demo hiring-channel metric has a pattern to show. That pattern is invented.

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
