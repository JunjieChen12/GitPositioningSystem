"""Bounded repository inspection and deterministic, parser-compatible routers."""

from collections import defaultdict
import codecs
from dataclasses import dataclass
import os
from pathlib import Path
import re
import subprocess
import time

from repo_router.generation import GenerationError, OpenRouterDescriptionModel, RouterDescriptionModel
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
MAX_ROUTERS = 24
MAX_BATCH_ENTRIES = 24
MAX_BATCH_CHARS = 24000
EXCERPT_BYTES = 1800


@dataclass(frozen=True)
class BootstrapResult:
    created: tuple[str, ...] = ()
    existing: bool = False


def find_repository_root(repository: str | Path) -> Path:
    try:
        result = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=repository,
                                capture_output=True, text=True, timeout=3, check=True)
        return Path(result.stdout.strip()).resolve(strict=True)
    except (OSError, subprocess.SubprocessError):
        raise GenerationError("cannot find Git repository root") from None


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
        # --exclude-standard alone does not exclude tracked files matching ignores.
        ignored = subprocess.run(["git", "check-ignore", "--no-index", "--stdin", "-z"],
                                 input=b"\0".join(os.fsencode(p) for p in paths) + b"\0",
                                 cwd=root, capture_output=True, timeout=5)
        if ignored.returncode not in (0, 1):
            raise GenerationError("cannot evaluate Git ignore rules")
        excluded = set(os.fsdecode(p) for p in ignored.stdout.split(b"\0") if p)
    except (OSError, subprocess.SubprocessError):
        raise GenerationError("cannot inspect repository") from None
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
        key = os.environ.get("OPENROUTER_API_KEY")
        if key and key in text:
            continue
        excerpts[path.as_posix()] = text + ("\n[excerpt truncated]" if len(raw) > EXCERPT_BYTES else "")
        if len(excerpts) >= MAX_FILES:
            break
    return excerpts


def _concise(value: object) -> str:
    if (not isinstance(value, str) or not value.strip() or len(value) > 300
            or any(ord(c) < 32 or ord(c) == 127 for c in value) or len(value.splitlines()) != 1
            or SECRET_CONTENT.search(value)):
        raise GenerationError("router model returned malformed descriptions")
    key = os.environ.get("OPENROUTER_API_KEY")
    if key and key in value:
        raise GenerationError("router model returned unsafe descriptions")
    return value.strip()


def bootstrap_repository(repository: str | Path, model: RouterDescriptionModel | None = None,
                         *, dry_run: bool = False, timeout: float = 75.0) -> BootstrapResult:
    root = find_repository_root(repository)
    root_router = root / "ROUTER.md"
    if root_router.exists() or root_router.is_symlink():
        try:
            if root_router.is_symlink():
                raise ValueError
            document = parse_router(root_router.read_text(encoding="utf-8"))
            if not (document.files or document.directories):
                raise ValueError
        except (OSError, ValueError):
            raise GenerationError("existing root ROUTER.md is unusable; repair it manually") from None
        return BootstrapResult(existing=True)

    deadline = time.monotonic() + timeout
    excerpts = inspect_repository(root)
    if not excerpts:
        raise GenerationError("no safe source files available for router generation")
    # Meaningful branching directories receive routers; single-file leaves are
    # represented directly in their nearest ancestor router.
    children = defaultdict(set)
    for name in excerpts:
        path = Path(name)
        for parent in path.parents:
            children[parent.as_posix()].add(path.relative_to(parent).parts[0])
    candidates = sorted((p for p, items in children.items() if p != "." and len(items) >= 2),
                        key=lambda p: (len(Path(p).parts), p))
    routers = {".", *candidates[:MAX_ROUTERS - 1]}
    entries = {directory: {} for directory in routers}
    for directory in sorted(routers - {"."}):
        parent = Path(directory).parent
        while parent.as_posix() not in routers:
            parent = parent.parent
        entries[parent.as_posix()][Path(directory).relative_to(parent).as_posix() + "/"] = directory
    for name in excerpts:
        parent = Path(name).parent
        while parent.as_posix() not in routers:
            parent = parent.parent
        entries[parent.as_posix()][Path(name).relative_to(parent).as_posix()] = name

    planned = tuple(sorted((Path(p) / "ROUTER.md").as_posix() for p in routers))
    if dry_run:
        return BootstrapResult(planned)
    model = model if model is not None else OpenRouterDescriptionModel()
    documents = {}
    summaries = {}
    # Children first: their compact summaries are used instead of repeated source.
    for directory in sorted(routers, key=lambda p: (-len(Path(p).parts), p)):
        descriptions = {}
        purpose = None
        batch = []
        size = 0

        def describe_batch():
            nonlocal purpose
            if time.monotonic() >= deadline:
                raise GenerationError("router generation time budget exceeded")
            if isinstance(model, OpenRouterDescriptionModel):
                model.timeout = min(model.timeout, max(0.1, deadline - time.monotonic()))
            try:
                response = model.describe({"directory": directory, "entries": list(batch)})
            except Exception:
                raise GenerationError("router description model failed") from None
            if not isinstance(response, dict) or not isinstance(response.get("descriptions"), dict):
                raise GenerationError("router model returned malformed output")
            expected = {entry["path"] for entry in batch}
            if set(response["descriptions"]) != expected:
                raise GenerationError("router model returned unexpected paths")
            sentence = _concise(response.get("purpose"))
            purpose = purpose or sentence
            descriptions.update({p: _concise(v) for p, v in response["descriptions"].items()})

        for relative, full in sorted(entries[directory].items()):
            item = {"path": relative, "kind": "directory" if relative.endswith("/") else "file",
                    "excerpt": summaries[full] if relative.endswith("/") else excerpts[full]}
            item_size = len(str(item))
            if batch and (len(batch) >= MAX_BATCH_ENTRIES or size + item_size > MAX_BATCH_CHARS):
                describe_batch()
                batch, size = [], 0
            batch.append(item)
            size += item_size
        if batch:
            describe_batch()
        lines = ["# Router", "", "## Purpose", purpose or "Repository source.", ""]
        for heading, is_dir in (("Directories", True), ("Important Files", False)):
            lines += [f"## {heading}"]
            lines += [f"- {p} — {descriptions[p]}" for p in sorted(descriptions) if p.endswith("/") == is_dir]
            lines.append("")
        text = "\n".join(lines)
        parse_router(text)
        documents[(Path(directory) / "ROUTER.md").as_posix()] = text
        summaries[directory] = (purpose or "") + " " + "; ".join(
            f"{p}: {v}" for p, v in sorted(descriptions.items()))[:6000]

    # Validate everything before writing; publish the root last as completion marker.
    if time.monotonic() >= deadline:
        raise GenerationError("router generation time budget exceeded")
    created = []
    try:
        for name in sorted(documents, key=lambda p: (p == "ROUTER.md", p)):
            destination = root / name
            if destination.exists() or destination.is_symlink():
                if destination.is_symlink():
                    raise GenerationError("refusing to write a symlink router")
                parse_router(destination.read_text(encoding="utf-8"))
                continue
            with destination.open("x", encoding="utf-8", newline="\n") as stream:
                created.append(destination)
                stream.write(documents[name])
    except (OSError, ValueError, GenerationError):
        for path in created:
            path.unlink(missing_ok=True)
        raise GenerationError("could not publish generated routers") from None
    return BootstrapResult(tuple(sorted(p.relative_to(root).as_posix() for p in created)))
