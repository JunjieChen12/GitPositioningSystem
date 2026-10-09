"""Stable, path-derived choice IDs shared by decision strategies."""

from collections.abc import Iterable
import re

from repo_router.decision import RouteEntry


def semantic_id(path: str) -> str:
    """Make an ASCII choice key from a route path."""
    identifier = re.sub(r"[^a-z0-9]+", "_", path.lower()).strip("_")
    if not identifier:
        identifier = f"path_{path.encode('utf-8').hex()}"
    if identifier[0].isdigit():
        identifier = f"path_{identifier}"
    return identifier


def entries_by_semantic_id(entries: Iterable[RouteEntry]) -> dict[str, RouteEntry]:
    """Map choice IDs to entries, suffixing collisions deterministically."""
    choices = list(entries)
    base_ids = [semantic_id(entry.path) for entry in choices]
    reserved_ids = set(base_ids)
    by_id: dict[str, RouteEntry] = {}
    for base_id, entry in zip(base_ids, choices):
        choice_id = base_id
        if choice_id in by_id:
            suffix = 2
            while True:
                choice_id = f"{base_id}_{suffix}"
                if choice_id not in reserved_ids and choice_id not in by_id:
                    break
                suffix += 1
        by_id[choice_id] = entry
    return by_id
