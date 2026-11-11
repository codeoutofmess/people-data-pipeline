"""Command-line entry point: `people <command>`."""

import argparse
from pathlib import Path

from people import auth, db, generate, log, pipeline, retention


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="people", description="Synthetic people-data pipeline.")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init-db", help="create the database tables")
    init.add_argument("--reset", action="store_true", help="DELETE all tables and data first")

    gen = sub.add_parser("generate", help="write the fake source CSV files")
    gen.add_argument("--seed", type=int, default=42)
    gen.add_argument("--out", type=Path, default=Path("data/raw"))

    run = sub.add_parser("run", help="validate the CSV files and load them into Postgres")
    run.add_argument("--raw", type=Path, default=Path("data/raw"))

    sub.add_parser("report", help="print the latest run's data-quality summary")

    seed = sub.add_parser("seed-users", help="create the 4 demo API users from DEMO_TOKEN_* vars")
    seed.add_argument("--manager", default="P0002", help="person_id the demo manager acts as")
    seed.add_argument("--employee", default="P0009", help="person_id the demo employee acts as")
    seed.add_argument("--days", type=int, default=30, help="days until the tokens expire")

    ret = sub.add_parser("retention", help="blank rejected candidates' name and email (demo rule)")
    ret.add_argument("--days", type=int, default=180, help="days after the decision")
    ret.add_argument("--dry-run", action="store_true", help="report only, change nothing")

    args = parser.parse_args(argv)
    log.setup()

    if args.command == "init-db":
        with db.connect() as conn:
            db.init_db(conn, reset=args.reset)
        print("Database wiped and tables recreated." if args.reset else "Tables created.")
    elif args.command == "generate":
        broken = generate.generate(args.out, seed=args.seed)
        print(f"Wrote 4 files to {args.out}/ (seed {args.seed}). Broken rows added: {broken}")
    elif args.command == "run":
        with db.connect() as conn:
            pipeline.run(conn, args.raw)
            print(pipeline.report(conn))
    elif args.command == "report":
        with db.connect() as conn:
            print(pipeline.report(conn))
    elif args.command == "seed-users":
        tokens = auth.tokens_from_env()
        with db.connect() as conn:
            auth.seed_users(conn, tokens, args.manager, args.employee, args.days)
        print(
            f"Seeded {', '.join(tokens)} (manager acts as {args.manager},"
            f" employee as {args.employee}),"
            f" tokens valid for {args.days} days."
        )
    elif args.command == "retention":
        with db.connect() as conn:
            ids = retention.redact_rejected(conn, args.days, dry_run=args.dry_run)
        verb = "Would blank" if args.dry_run else "Blanked"
        print(f"{verb} name and email for {len(ids)} rejected candidates ({args.days}+ days old).")
