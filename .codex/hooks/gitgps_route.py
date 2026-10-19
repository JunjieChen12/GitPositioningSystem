"""Inject optional Git GPS starting locations; routing must never block a prompt."""

import json
import os
from pathlib import Path
import shutil
import shlex
import subprocess
import sys


STATUSES = {"found", "no_match", "missing_router", "missing_target",
            "max_depth", "cycle", "budget_exceeded"}
MAX_LOCATIONS = 16


def routing_context(output: str, repo_root: Path) -> str:
    """Validate the JSON contract and keep only safe repository locations."""
    if len(output) > 65536:
        raise ValueError("oversized response")
    data = json.loads(output)
    if not isinstance(data, dict) or data.get("status") not in STATUSES:
        raise ValueError("invalid status")
    sections = []
    for field, title in (("relevant_paths", "Relevant paths"), ("search_roots", "Search roots")):
        values = data.get(field)
        if not isinstance(values, list):
            raise ValueError("invalid paths")
        locations = []
        for value in values:
            if (not isinstance(value, str) or not value or len(value) > 240
                    or any(ord(char) < 32 or ord(char) == 127 for char in value)):
                raise ValueError("invalid path")
            path = Path(value)
            target = (repo_root / path).resolve()
            if path.is_absolute() or not target.is_relative_to(repo_root):
                raise ValueError("path outside repository")
            if field == "relevant_paths" and not target.is_file():
                continue
            if field == "search_roots" and not target.is_dir():
                continue
            normalized = target.relative_to(repo_root).as_posix()
            if normalized not in locations and len(locations) < MAX_LOCATIONS:
                locations.append(normalized)
        if locations:
            sections.append(title + ":\n" + "\n".join(f"- {path}" for path in locations))
    if not sections:
        return ""
    context = "Git GPS routing context:\n\n" + "\n\n".join(sections) + (
        "\n\nPaths are relative to the repository root: " + str(repo_root) +
        "\nThese are suggested starting locations, not guaranteed edit targets.\n"
        "Start with these Git GPS routed locations. Inspect them before doing a broad "
        "repository search. Expand beyond them if the route is insufficient."
    )
    # Even a malformed executable response must not echo the inherited secret.
    if any(value and value in context for name, value in os.environ.items()
           if any(word in name.upper() for word in ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL"))):
        raise ValueError("unsafe response")
    return context


def bootstrap_context(repo_root: Path, executable: str) -> str:
    """Delegate preparation to the current coding turn, with no nested process."""
    return """Git GPS bootstrap required.

This repository does not contain a root ROUTER.md.

Before completing the user's task, CODEX must do the following in this SAME turn:
1. Inspect the repository structure using your existing repository tools.
2. Read the important source files necessary to understand each major directory.
3. Create a root ROUTER.md based on actual file contents and responsibilities.
4. Create nested ROUTER.md files only where hierarchical routing is useful, such as
   src/, tests/, frontend/, or backend/. Do not create one in every tiny directory.
   Preserve existing nested routers.
5. Keep descriptions concise and functional. Avoid labels like "Python file" or
   "source files"; describe the behavior evidenced by the code.
6. Respect .gitignore where practical. Do not inspect or summarize obvious secrets
   (.env, private keys, credentials, API tokens, or secret files). Exclude generated,
   ignored, binary, and secret files from routers. Never put secrets into ROUTER.md.
7. After creating routers, run Git GPS on the user's ORIGINAL task from the
   repository root shown below. Pass the original task unchanged as ONE argument
   in place of <original task>; quote it safely or use a subprocess argument list.
8. Inspect the returned relevant_paths and search_roots before broad repository
   search. Use those locations as the starting point for the task.
9. Continue and complete the user's ORIGINAL request. Router creation is only a
   preparation step and must not replace the user's task.

Use this supported format (paths are relative to the containing router):

# Router

## Purpose
Concise description of this repository/directory.

## Directories
- src/ — production source code and major application logic
- tests/ — automated tests

## Important Files
- main.py — FastAPI application startup and note CRUD endpoints
- models.py — persistent application data models

These entries are examples; include only real paths and descriptions verified by
reading this repository's files. A useful directory description might be:
- static/ — browser-side calendar rendering, note interactions, and API requests

Use the current Codex model and tools. Do not call a general-purpose external
model/API, change model configuration, or launch codex/codex exec recursively.
If Git GPS cannot run, report a concise diagnostic and continue the original task.
The original user task is already available in this turn; it is not replaced here.

Repository root: """ + json.dumps(str(repo_root)) + """
Run after router creation:
""" + shlex.quote(executable) + ' route . "<original task>" --multi --top-k 4 --threshold 0.5 --json'


def main() -> int:

    try:
        event = json.load(sys.stdin)
        if not isinstance(event, dict):
            return 0
        prompt = event.get("prompt")
        cwd = event.get("cwd")
        if not isinstance(prompt, str) or not prompt.strip():
            return 0
        if not isinstance(cwd, str) or not cwd.strip():
            return 0

        root_result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"], cwd=cwd,
            capture_output=True, text=True, timeout=3,
        )
        if root_result.returncode:
            return 0
        root_text = root_result.stdout.strip()
        if not root_text or not Path(root_text).is_absolute():
            return 0
        repo_root = Path(root_text).resolve()
        executable = os.environ.get("GIT_GPS_EXECUTABLE")
        if executable:
            executable = os.path.expanduser(executable)
            if not Path(executable).is_absolute() and os.sep in executable:
                executable = str((repo_root / executable).resolve())
        else:
            executable = shutil.which("git-gps")
        if not (repo_root / "ROUTER.md").exists():
            print(bootstrap_context(repo_root, executable or "git-gps"))
            if not executable:
                print("Git GPS hook: executable unavailable; routing requires git-gps or GIT_GPS_EXECUTABLE.",
                      file=sys.stderr)
            return 0
        if not executable:
            print("Git GPS hook: executable unavailable; skipping routing.", file=sys.stderr)
            return 0
        result = subprocess.run(
            [executable, "route", ".", prompt, "--strategy", "jev", "--multi",
             "--top-k", "4", "--threshold", "0.5", "--json"],
            cwd=repo_root, capture_output=True, text=True, timeout=25,
        )
        if result.returncode:
            print("Git GPS hook: routing unavailable; continuing.", file=sys.stderr)
            return 0
        context = routing_context(result.stdout, repo_root)
        if context:
            print(context)
    except subprocess.TimeoutExpired:
        print("Git GPS hook: routing timed out; continuing.", file=sys.stderr)
    except Exception:
        # Never echo exception text, subprocess stderr, the prompt, or environment.
        print("Git GPS hook: routing unavailable; continuing.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
