# Git Positioning System

## What it does

Git GPS reads hierarchical `ROUTER.md` files and uses routing strategies such as Jev to identify likely relevant files and directories for a coding task. It returns suggested paths or search roots for an agent to inspect before performing a broader repository search.

## Installation

Requires Python 3.10 or newer.

```sh
git clone https://github.com/JunjieChen12/GitPositioningSystem.git
cd GitPositioningSystem

python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

On Windows PowerShell, activate with `.\.venv\Scripts\Activate.ps1`.

## Jev / OpenRouter setup

Jev requires an OpenRouter API key:

```sh
export OPENROUTER_API_KEY="sk-or-v1-..."
```

Optionally, add this executable setting to your shell profile so the Codex hook can call Git GPS without manually activating the virtual environment. Adjust the path to your checkout:

```sh
export GIT_GPS_EXECUTABLE="$HOME/GitPositioningSystem/.venv/bin/git-gps"
```

## Usage

Run these commands from a repository containing a root `ROUTER.md`.

Single route:

```sh
git-gps route . "Where is Jev implemented?" --strategy jev --trace
```

Multiple routes:

```sh
git-gps route . \
  "Update Jev multi-route behavior and add tests" \
  --multi \
  --top-k 4 \
  --threshold 0.5 \
  --trace
```

Normal routing returns the best route. `--multi` uses Jev by default and returns multiple relevant locations. `--top-k` limits candidates selected per router, `--threshold` filters low-relevance routes, and `--trace` shows the routing path.

## ROUTER.md

```markdown
# Router

## Purpose
Core source code for the project.

## Directories
- src/repo_router/ — routing engine, CLI, Jev integration, and routing strategies

## Important Files
- README.md — project documentation
```

Git GPS uses these files hierarchically: a root router lists initial locations, and nested routers narrow the task to more specific paths. Paths are relative to the router that lists them.

## Codex Hook

The repository includes a `UserPromptSubmit` hook in `.codex/hooks.json`. It receives the prompt, runs Git GPS, and injects relevant paths and search roots into Codex context.

Before launching Codex, ensure `OPENROUTER_API_KEY` is available and set:

```sh
export GIT_GPS_EXECUTABLE="$HOME/GitPositioningSystem/.venv/bin/git-gps"
```

Adjust the path to your checkout, and enable and trust the hook through Codex's `/hooks` menu. For another repository, copy `.codex/hooks.json` and `.codex/hooks/gitgps_route.py` into it and provide a root `ROUTER.md`.

## Testing

```sh
source .venv/bin/activate
python -m pytest
```
