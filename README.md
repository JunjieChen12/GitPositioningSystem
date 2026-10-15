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
export OPENROUTER_API_KEY="<your-openrouter-api-key>"
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

Adjust the path to your checkout, and enable and trust the hook through Codex's `/hooks` menu. For another repository, copy `.codex/hooks.json` and `.codex/hooks/gitgps_route.py` into it. Missing routers are generated automatically.

## Automatic router generation

When the Codex hook starts in a repository without a root `ROUTER.md`, Git GPS inspects safe source excerpts with a small model, generates a router hierarchy, and then uses Jev to route the original task:

```text
Codex prompt → generate routers if missing → Jev routing → Codex coding
```

Manual bootstrap works from the repository root or a subdirectory:

```sh
git-gps init . --dry-run  # list planned routers; no model calls or writes
git-gps init .            # create ROUTER.md and useful nested routers
```

Generation uses the existing `OPENROUTER_API_KEY`. Set `GIT_GPS_ROUTER_MODEL` to select the generation model independently of Jev; the default is [`google/gemini-2.5-flash-lite`](https://openrouter.ai/google/gemini-2.5-flash-lite/). No main coding model is used. `GIT_GPS_EXECUTABLE` selects the installed CLI for the hook. Optionally set `GIT_GPS_ROUTER_URL` to another compatible chat-completions endpoint (the same API key is sent there).

```sh
export GIT_GPS_ROUTER_MODEL="google/gemini-2.5-flash-lite"
```

Inspection respects Git ignores, including ignored tracked files, and excludes generated directories, obvious secret files, symlinks, binaries, and excerpts containing recognizable credentials. Requests batch bounded excerpts and child summaries, with limits of 600 inspected files, 24 routers, and 75 seconds of generation. Large repositories may receive partial coverage. Descriptions stay concise and all generated routers are parsed before writing; the root is written last.

Existing root routers skip generation entirely. Manual initialization checks their format; unusable existing routers need manual repair. Existing nested routers are preserved. Bootstrap or routing failures emit a short diagnostic and Codex continues with the original prompt. The hook allows up to 90 seconds for bootstrap and 25 seconds for routing. It injects only routed paths and search roots. This feature does not synchronize existing routers.

## Testing

```sh
source .venv/bin/activate
python -m pytest
```
