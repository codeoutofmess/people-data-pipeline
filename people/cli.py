"""Command-line entry point: `people <command>`."""

import argparse
from pathlib import Path

from people import db, generate


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="people", description="Synthetic people-data pipeline.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="create the database tables")

    gen = sub.add_parser("generate", help="write the fake source CSV files")
    gen.add_argument("--seed", type=int, default=42)
    gen.add_argument("--out", type=Path, default=Path("data/raw"))

    args = parser.parse_args(argv)

    if args.command == "init-db":
        with db.connect() as conn:
            db.init_db(conn)
        print("Tables created.")
    elif args.command == "generate":
        broken = generate.generate(args.out, seed=args.seed)
        print(f"Wrote 4 files to {args.out}/ (seed {args.seed}). Broken rows added: {broken}")
