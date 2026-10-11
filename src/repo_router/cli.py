"""Command-line interface for git-gps."""

import argparse
import sys

from repo_router.decision import KeywordRoutingStrategy
from repo_router.engine import RouteStatus, route_repository, route_repository_multi
from repo_router.jev import JevError, JevRoutingStrategy
from repo_router.laya import LayaError, LayaRoutingStrategy
from repo_router.router import RouterError, read_root_router


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def _probability(value: str) -> float:
    parsed = float(value)
    if not 0 <= parsed <= 1:
        raise argparse.ArgumentTypeError("must be between 0 and 1")
    return parsed


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
        choices=("keyword", "laya", "jev"),
        default="keyword",
        help="Routing strategy (default: keyword)",
    )
    route.add_argument("--trace", action="store_true", help="Print each routing decision")
    route.add_argument("--multi", action="store_true", help="Find multiple relevant paths (Jev only)")
    route.add_argument("--top-k", type=_positive_int, default=3, help="Maximum choices per router (default: 3)")
    route.add_argument("--threshold", type=_probability, help="Minimum route probability")
    route.add_argument("--max-nodes", type=_positive_int, default=20, help="Multi-route node budget (default: 20)")
    route.add_argument("--max-depth", type=int, default=10, help="Maximum directory transitions (default: 10)")
    args = parser.parse_args(argv)
    if args.command == "route":
        if args.multi and args.strategy != "jev":
            parser.error("--multi requires --strategy jev")
        if args.max_depth < 0:
            parser.error("--max-depth must be non-negative")

    if args.command == "inspect":
        try:
            sys.stdout.write(read_root_router(args.repository))
        except RouterError as exc:
            print(f"git-gps: {exc}", file=sys.stderr)
            return 1
    elif args.command == "route":
        if args.strategy == "keyword":
            strategy = KeywordRoutingStrategy()
        elif args.strategy == "laya":
            strategy = LayaRoutingStrategy()
        else:
            strategy = JevRoutingStrategy()
        try:
            if args.multi:
                result = route_repository_multi(
                    args.repository, args.task, strategy, top_k=args.top_k,
                    threshold=args.threshold, max_nodes=args.max_nodes, max_depth=args.max_depth,
                )
            else:
                result = route_repository(args.repository, args.task, strategy, max_depth=args.max_depth)
        except (RouterError, LayaError, JevError) as exc:
            print(f"git-gps: {exc}", file=sys.stderr)
            return 1
        if args.trace:
            print("route trace:")
            for step in result.trace:
                metadata = step.metadata or {}
                label = step.selected_path or f"({metadata.get('status', 'no match')})"
                probability = metadata.get("probability") if args.multi else None
                suffix = f" ({probability:g})" if probability is not None else ""
                print(f"{step.current_directory} -> {label}{suffix}")
            print()
        print(f"status: {result.status.value}")
        if args.multi:
            print("relevant paths:")
            for path in result.paths:
                print(f"- {path}")
            if result.search_roots:
                print("search roots:")
                for directory in result.search_roots:
                    print(f"- {directory}")
            return 0
        if result.status == RouteStatus.FOUND:
            print(f"path: {result.path}")
        print(f"search_root: {result.search_root}")
    return 0
