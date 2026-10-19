"""Bounded, local repository inspection for coding-agent router bootstrap."""

import codecs
import os
from pathlib import Path
import re
import subprocess

from repo_router.parser import parse_router

SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", "build", "dist",
             "coverage", "target", ".pytest_cache", ".mypy_cache", ".next", ".ssh"}
TEXT_SUFFIXES = {".py", ".js", ".jsx", ".ts", ".tsx", ".html", ".css", ".scss", ".go",
                 ".rs", ".java", ".kt", ".c", ".h", ".cpp", ".cs", ".rb", ".php", ".sh",
                 ".sql", ".graphql", ".proto", ".json", ".toml", ".yaml", ".yml", ".md",
                 ".txt", ".ini", ".cfg", ".vue", ".svelte", ".ex", ".exs", ".swift"}
SECRET_NAME = re.compile(r"(^|[._-])(secrets?|credentials?|tokens?|api[_-]?keys?|private([._-]?key)?)([._-]|$)", re.I)
SECRET_CONTENT = re.compile(
    r"-----BEGIN .*PRIVATE KEY-----|(?:api[_-]?key|(?:access[_-]?)?token|password|secret)\s*[:=]\s*"
    r"(?:[\"'][^\"'\n]{8,}[\"']|[A-Za-z0-9/+_-]{12,})|sk-or-v1-[\w-]+",
    re.I,
)
MAX_FILES = 600
EXCERPT_BYTES = 1800


class BootstrapError(Exception):
    """Local repository inspection failed."""


def find_repository_root(repository: str | Path) -> Path:
    try:
        result = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=repository,
                                capture_output=True, text=True, timeout=3, check=True)
        return Path(result.stdout.strip()).resolve(strict=True)
    except (OSError, subprocess.SubprocessError):
        raise BootstrapError("cannot find Git repository root") from None


def _safe_path(path: Path) -> bool:
    for part in path.parts:
        lower = part.lower()
        if (lower in SKIP_DIRS or lower == ".env" or lower.startswith(".env.")
                or SECRET_NAME.search(part) or lower.startswith(("id_rsa", "id_dsa", "id_ed25519", "id_ecdsa"))
                or "service-account" in lower or "service_account" in lower
                or lower.endswith((".pem", ".key", ".p12", ".pfx", ".jks", ".keystore"))
                or any(ord(c) < 32 or ord(c) == 127 for c in part) or len(part.splitlines()) != 1
                or " — " in part or "\\" in part):
            return False
    return not path.is_absolute() and ".." not in path.parts


def inspect_repository(root: Path) -> dict[str, str]:
    """List Git-visible files first, then read only bounded, safe text excerpts."""
    try:
        result = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
                                cwd=root, capture_output=True, timeout=5, check=True)
        paths = sorted(set(os.fsdecode(p) for p in result.stdout.split(b"\0") if p),
                       key=lambda p: (len(Path(p).parts), p))
        if not paths:
            return {}
        # --exclude-standard alone does not exclude tracked files matching ignores.
        ignored = subprocess.run(["git", "check-ignore", "--no-index", "--stdin", "-z"],
                                 input=b"\0".join(os.fsencode(p) for p in paths) + b"\0",
                                 cwd=root, capture_output=True, timeout=5)
        if ignored.returncode not in (0, 1):
            raise BootstrapError("cannot evaluate Git ignore rules")
        excluded = set(os.fsdecode(p) for p in ignored.stdout.split(b"\0") if p)
    except (OSError, subprocess.SubprocessError):
        raise BootstrapError("cannot inspect repository") from None
    excerpts = {}
    for name in paths:
        path = Path(name)
        if name in excluded or not _safe_path(path) or path.name == "ROUTER.md":
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES and path.name not in {"Dockerfile", "Makefile", "Procfile"}:
            continue
        source = root / path
        # Reject symlinks in both the file and its parent directories.
        if any(p.is_symlink() for p in (source, *source.parents)) or not source.is_file():
            continue
        try:
            with source.open("rb") as stream:
                raw = stream.read(EXCERPT_BYTES + 1)
            if b"\0" in raw:
                continue
            # A truncated final UTF-8 character is safe to omit, but invalid text is not.
            text = codecs.getincrementaldecoder("utf-8")().decode(raw[:EXCERPT_BYTES], final=False)
        except (OSError, UnicodeError):
            continue
        if any(ord(c) < 32 and c not in "\t\r\n" for c in text):
            continue
        if SECRET_CONTENT.search(text):
            continue
        excerpts[path.as_posix()] = text + ("\n[excerpt truncated]" if len(raw) > EXCERPT_BYTES else "")
        if len(excerpts) >= MAX_FILES:
            break
    return excerpts



def bootstrap_repository(repository: str | Path) -> dict:
    """Return an inspection request; never write routers or call a model/API.

    Candidate paths are bounded and contain no source excerpts. The coding agent
    must read actual files before describing their responsibilities.
    """
    root = find_repository_root(repository)
    router = root / "ROUTER.md"
    if router.exists() or router.is_symlink():
        try:
            if router.is_symlink():
                raise ValueError
            document = parse_router(router.read_text(encoding="utf-8"))
            if not (document.files or document.directories):
                raise ValueError
        except (OSError, ValueError):
            raise BootstrapError("existing root ROUTER.md is unusable; repair it manually") from None
        return {"status": "router_exists", "repository_root": str(root),
                "suggested_directories": [], "files_to_inspect": []}

    files = inspect_repository(root)
    directories = sorted({parent.as_posix() for name in files
                          for parent in Path(name).parents if parent != Path(".")},
                         key=lambda p: (len(Path(p).parts), p))
    return {"status": "router_required", "repository_root": str(root),
            "suggested_directories": directories, "files_to_inspect": list(files)}
