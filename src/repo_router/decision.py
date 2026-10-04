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
