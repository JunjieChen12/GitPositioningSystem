"""Traverse repository router documents with a supplied decision strategy."""

from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from repo_router.decision import MultiRoutingStrategy, RoutingStrategy, validate_multi_selection
from repo_router.parser import DirectoryEntry, parse_router
from repo_router.router import RouterError, read_root_router
from repo_router.route_ids import entries_by_semantic_id


class RouteStatus(str, Enum):
    FOUND = "found"
    NO_MATCH = "no_match"
    MISSING_ROUTER = "missing_router"
    MISSING_TARGET = "missing_target"
    MAX_DEPTH = "max_depth"
    CYCLE = "cycle"
    BUDGET_EXCEEDED = "budget_exceeded"


@dataclass(frozen=True)
class RouteTraceStep:
    current_directory: str
    selected_path: str | None
    metadata: Mapping[str, object] | None = None


@dataclass(frozen=True)
class RouteResult:
    """A route outcome and its observed decisions.

    Trace is excluded from equality to preserve comparisons of existing result fields.
    """

    status: RouteStatus
    path: str | None
    search_root: str
    trace: tuple[RouteTraceStep, ...] = field(default_factory=tuple, compare=False)


def route_repository(
    repository: str | Path,
    task: str,
    strategy: RoutingStrategy,
    *,
    max_depth: int = 10,
) -> RouteResult:
    """Route from the root document to a file or a useful search directory.

    ``max_depth`` limits directory transitions after the root ROUTER.md.
    Paths in the result are relative to the repository; ``search_root`` is
    the best directory from which a broader search can continue.
    """
    if max_depth < 0:
        raise ValueError("max_depth must be non-negative")

    root = Path(repository).resolve()
    contents = read_root_router(root)
    current = root
    visited: set[Path] = set()
    trace: list[RouteTraceStep] = []
    depth = 0

    def result(status: RouteStatus, path: str | None, search_root: str) -> RouteResult:
        return RouteResult(status, path, search_root, tuple(trace))

    while True:
        visited.add(current)
        document = parse_router(contents)
        choice = strategy.decide(task, [*document.directories, *document.files])
        current_relative = current.relative_to(root).as_posix()
        metadata_provider = getattr(strategy, "decision_metadata", None)
        metadata = metadata_provider() if callable(metadata_provider) else None
        trace.append(
            RouteTraceStep(
                current_relative,
                choice.path if choice is not None else None,
                dict(metadata) if metadata else None,
            )
        )

        if choice is None:
            return result(RouteStatus.NO_MATCH, None, current_relative)

        target = (current / choice.path).resolve()
        if not target.is_relative_to(root):
            raise RouterError(f"Route path leaves repository: {choice.path}")
        target_relative = target.relative_to(root).as_posix()

        if not isinstance(choice, DirectoryEntry):
            if not target.is_file():
                return result(
                    RouteStatus.MISSING_TARGET,
                    None,
                    current_relative,
                )

            return result(
                RouteStatus.FOUND,
                target_relative,
                current_relative,
            )

        if target in visited:
            return result(RouteStatus.CYCLE, None, current_relative)
        if depth >= max_depth:
            return result(RouteStatus.MAX_DEPTH, None, target_relative)

        router = target / "ROUTER.md"
        if not router.is_file():
            return result(RouteStatus.MISSING_ROUTER, None, target_relative)
        try:
            contents = router.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise RouterError(f"Cannot read ROUTER.md at {router}: {exc}") from exc

        current = target
        depth += 1


@dataclass(frozen=True)
class MultiRouteResult:
    """Deduplicated files and fallback directories, with individual outcomes.

    Budget exhaustion takes status precedence, then any found file, otherwise
    the first branch outcome. ``branches`` retains all terminal statuses.
    """

    status: RouteStatus
    paths: list[str]
    search_roots: list[str]
    trace: tuple[RouteTraceStep, ...] = ()
    branches: tuple[RouteResult, ...] = ()
    metadata: Mapping[str, object] | None = None


def route_repository_multi(
    repository: str | Path, task: str, strategy: MultiRoutingStrategy, *,
    top_k: int | None = 3, threshold: float | None = None,
    max_depth: int = 10, max_nodes: int = 20,
) -> MultiRouteResult:
    """Explore selected branches breadth-first, preserving probability order.

    Selection filters by threshold before applying top_k at each router.
    max_depth limits directory transitions just as in route_repository.
    max_nodes counts the root plus every selected target (including duplicates
    and failed targets). No new targets are followed once this budget is used.
    Search roots are fallback directories for branches that did not find files.
    """
    validate_multi_selection(top_k, threshold)
    if max_depth < 0:
        raise ValueError("max_depth must be non-negative")
    if isinstance(max_nodes, bool) or not isinstance(max_nodes, int) or max_nodes <= 0:
        raise ValueError("max_nodes must be a positive integer")
    root = Path(repository).resolve()
    contents = read_root_router(root)
    queue = deque([(root, contents, 0, frozenset({root}))])
    paths: dict[str, None] = {}
    search_roots: dict[str, None] = {}
    trace: list[RouteTraceStep] = []
    branches: list[RouteResult] = []
    nodes = 1
    exhausted = False

    def finish(status: RouteStatus, directory: str, path: str | None = None) -> None:
        branches.append(RouteResult(status, path, directory))
        if path is not None:
            paths[path] = None
        else:
            search_roots[directory] = None

    while queue and not exhausted:
        current, contents, depth, visited = queue.popleft()
        relative = current.relative_to(root).as_posix()
        document = parse_router(contents)
        entries = [*document.directories, *document.files]
        selected = strategy.decide_many(task, entries, top_k=top_k, threshold=threshold)
        provider = getattr(strategy, "decision_metadata", None)
        metadata = provider() if callable(provider) else None
        # Enforce the supplied-entry boundary for every multi strategy.
        selected = list(dict.fromkeys(entry for entry in selected if entry in entries))
        if not selected:
            trace.append(RouteTraceStep(relative, None, dict(metadata) if metadata else None))
            finish(RouteStatus.NO_MATCH, relative)
        by_id = entries_by_semantic_id(entries)
        for entry in selected:
            if nodes >= max_nodes:
                exhausted = True
                trace.append(RouteTraceStep(relative, None, {"status": "budget_exceeded"}))
                finish(RouteStatus.BUDGET_EXCEEDED, relative)
                break
            nodes += 1
            step_metadata = dict(metadata) if metadata else {}
            probabilities = step_metadata.get("probabilities")
            if isinstance(probabilities, Mapping):
                choice_id = next(key for key, value in by_id.items() if value == entry)
                probability = probabilities.get(choice_id)
                if (isinstance(probability, (int, float)) and not isinstance(probability, bool)
                        and 0 <= probability <= 1):
                    step_metadata["probability"] = probability
            trace.append(RouteTraceStep(relative, entry.path, step_metadata or None))
            target = (current / entry.path).resolve()
            if not target.is_relative_to(root):
                raise RouterError(f"Route path leaves repository: {entry.path}")
            target_relative = target.relative_to(root).as_posix()
            if not isinstance(entry, DirectoryEntry):
                if target.is_file():
                    finish(RouteStatus.FOUND, relative, target_relative)
                else:
                    finish(RouteStatus.MISSING_TARGET, relative)
            elif not target.is_dir():
                finish(RouteStatus.MISSING_TARGET, relative)
            elif target in visited:
                finish(RouteStatus.CYCLE, relative)
            elif depth >= max_depth:
                finish(RouteStatus.MAX_DEPTH, target_relative)
            elif not (target / "ROUTER.md").is_file():
                finish(RouteStatus.MISSING_ROUTER, target_relative)
            else:
                nested = read_root_router(target)
                queue.append((target, nested, depth + 1, visited | {target}))

    if exhausted:
        for current, _, _, _ in queue:
            finish(RouteStatus.BUDGET_EXCEEDED, current.relative_to(root).as_posix())
    status = (RouteStatus.BUDGET_EXCEEDED if exhausted else
              RouteStatus.FOUND if paths else branches[0].status)
    return MultiRouteResult(status, list(paths), list(search_roots), tuple(trace),
                            tuple(branches), {"nodes_visited": nodes, "max_nodes": max_nodes})
