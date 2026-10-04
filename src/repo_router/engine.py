"""Traverse repository router documents with a supplied decision strategy."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from repo_router.decision import RoutingStrategy
from repo_router.parser import DirectoryEntry, parse_router
from repo_router.router import RouterError, read_root_router


class RouteStatus(str, Enum):
    FOUND = "found"
    NO_MATCH = "no_match"
    MISSING_ROUTER = "missing_router"
    MISSING_TARGET = "missing_target"
    MAX_DEPTH = "max_depth"
    CYCLE = "cycle"


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
