"""Command-line interface for git-gps."""

import argparse
import sys

from repo_router.decision import KeywordRoutingStrategy
from repo_router.engine import RouteStatus, route_repository
from repo_router.laya import LayaError, LayaRoutingStrategy
from repo_router.router import RouterError, read_root_router


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="git-gps")
    commands = parser.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser("inspect", help="Print the root ROUTER.md")
    inspect.add_argument("repository", help="Repository directory to inspect")
    route = commands.add_parser("route", help="Find a relevant file for a task")
    route.add_argument("repository", help="Repository directory to route through")
    route.add_argument("task", help="Natural-language task")
    route.add_argument(
        "--strategy",
        choices=("keyword", "laya"),
        default="keyword",
        help="Routing strategy (default: keyword)",
    )
    route.add_argument("--trace", action="store_true", help="Print each routing decision")
    args = parser.parse_args(argv)

    if args.command == "inspect":
        try:
            sys.stdout.write(read_root_router(args.repository))
        except RouterError as exc:
            print(f"git-gps: {exc}", file=sys.stderr)
            return 1
    elif args.command == "route":
        strategy = KeywordRoutingStrategy() if args.strategy == "keyword" else LayaRoutingStrategy()
        try:
            result = route_repository(args.repository, args.task, strategy)
        except (RouterError, LayaError) as exc:
            print(f"git-gps: {exc}", file=sys.stderr)
            return 1
        if args.trace:
            print("route trace:")
            for step in result.trace:
                print(f"{step.current_directory} -> {step.selected_path or '(no match)'}")
            print()
        print(f"status: {result.status.value}")
        if result.status == RouteStatus.FOUND:
            print(f"path: {result.path}")
        print(f"search_root: {result.search_root}")
    return 0
