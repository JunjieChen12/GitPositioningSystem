"""Command-line interface for git-gps."""

import argparse
import sys

from repo_router.router import RouterError, read_root_router


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="git-gps")
    commands = parser.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser("inspect", help="Print the root ROUTER.md")
    inspect.add_argument("repository", help="Repository directory to inspect")
    args = parser.parse_args(argv)

    if args.command == "inspect":
        try:
            sys.stdout.write(read_root_router(args.repository))
        except RouterError as exc:
            print(f"git-gps: {exc}", file=sys.stderr)
            return 1
    return 0
