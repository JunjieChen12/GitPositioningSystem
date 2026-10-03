"""Access router documents in a repository."""

from pathlib import Path


class RouterError(Exception):
    """A repository router document could not be read."""


def read_root_router(repository: str | Path) -> str:
    """Return the contents of ROUTER.md at the repository root."""
    root = Path(repository)
    if not root.is_dir():
        raise RouterError(f"Repository directory does not exist: {root}")

    router = root / "ROUTER.md"
    if not router.is_file():
        raise RouterError(f"ROUTER.md does not exist in repository directory: {root}")

    try:
        return router.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise RouterError(f"Cannot read ROUTER.md at {router}: {exc}") from exc
