"""Choose a route entry for a task without coupling callers to a strategy."""

from collections.abc import Iterable, Mapping
from typing import Protocol, TypeAlias
import re

from repo_router.parser import DirectoryEntry, FileEntry


RouteEntry: TypeAlias = DirectoryEntry | FileEntry


class RoutingStrategy(Protocol):
    """Interface for selecting one entry from a set of possible routes."""

    def decide(self, task: str, entries: Iterable[RouteEntry]) -> RouteEntry | None:
        """Return the most relevant entry, or None when none matches."""


class DecisionMetadataProvider(Protocol):
    """Optional strategy hook for metadata about the most recent decision."""

    def decision_metadata(self) -> Mapping[str, object] | None:
        """Return metadata for the most recent call to decide, if available."""


class MultiRoutingStrategy(Protocol):
    """Select several supplied entries without changing single-route strategies."""

    def decide_many(
        self, task: str, entries: Iterable[RouteEntry], *,
        top_k: int | None = 3, threshold: float | None = None,
    ) -> list[RouteEntry]:
        """Rank entries, filter by threshold, then take at most top_k."""


def validate_multi_selection(top_k: int | None, threshold: float | None) -> None:
    if top_k is not None and (isinstance(top_k, bool) or not isinstance(top_k, int) or top_k <= 0):
        raise ValueError("top_k must be a positive integer or None")
    if threshold is not None and (
        isinstance(threshold, bool)
        or not isinstance(threshold, (int, float))
        or not 0 <= threshold <= 1
    ):
        raise ValueError("threshold must be between 0 and 1")


_WORDS = re.compile(r"[a-z0-9]+", re.IGNORECASE)
_STOP_WORDS = frozenset(
    {"a", "an", "and", "are", "for", "in", "is", "of", "on", "or", "the", "to", "where"}
)


def _keywords(text: str) -> set[str]:
    return {word.lower() for word in _WORDS.findall(text)} - _STOP_WORDS


class KeywordRoutingStrategy:
    """Score shared keywords, giving path matches more weight than descriptions."""

    def decide(self, task: str, entries: Iterable[RouteEntry]) -> RouteEntry | None:
        task_words = _keywords(task)
        best: RouteEntry | None = None
        best_score = 0

        for entry in entries:
            score = 2 * len(task_words & _keywords(entry.path))
            score += len(task_words & _keywords(entry.description))
            if score > best_score:
                best = entry
                best_score = score

        return best
