"""Inject optional Git GPS starting locations; routing must never block a prompt."""

import json
import os
from pathlib import Path
import shutil
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
    secret = os.environ.get("OPENROUTER_API_KEY")
    if secret and secret in context:
        raise ValueError("unsafe response")
    return context


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
        if not executable:
            print("Git GPS hook: executable unavailable; skipping routing.", file=sys.stderr)
            return 0
        if not (repo_root / "ROUTER.md").exists():
            bootstrap = subprocess.run(
                [executable, "init", "."], cwd=repo_root,
                capture_output=True, text=True, timeout=90,
            )
            if bootstrap.returncode or not (repo_root / "ROUTER.md").is_file():
                print("Git GPS hook: router bootstrap unavailable; continuing.", file=sys.stderr)
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
