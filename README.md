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

Adjust the path to your checkout, and enable and trust the hook through Codex's `/hooks` menu. For another repository, copy `.codex/hooks.json` and `.codex/hooks/gitgps_route.py` into it. When routers are missing, the hook delegates their creation to the current Codex turn.

## Router bootstrap by Codex

Git GPS does not use a general-purpose external LLM to generate `ROUTER.md` files.
OpenRouter is only required for Jev routing through the Decisions API;
`OPENROUTER_API_KEY` is used exclusively by the Jev implementation.

With an existing root router:

```text
Codex prompt → Git GPS / Jev routing → routed context → Codex performs task
```

Without a root router:

```text
Codex prompt → hook asks current Codex turn to create routers
            → Codex reads repository files and writes ROUTER.md
            → Git GPS / Jev routes original task → Codex performs task
```

Codex uses its current model and repository tools to read actual source files and
write concise descriptions of their responsibilities. Nested routers are useful
for major branching directories, not every tiny directory. Existing root routers
route immediately without a request to regenerate them. Bootstrap is preparation;
it never replaces the original coding task. No nested Codex process is launched
and no model configuration is changed.

After creating routers, Codex runs from the repository root:

```sh
"$GIT_GPS_EXECUTABLE" route . "<original task>" --multi --top-k 4 --threshold 0.5 --json
```

Use `git-gps` if no executable override is configured. Pass the original task
unchanged as one safely quoted argument. Inspect `relevant_paths` and
`search_roots` before broad search, then complete the original request.

Optional deterministic inspection works from a repository root or subdirectory:

```sh
git-gps init .         # report candidate paths; coding agent creates routers
git-gps init . --json  # machine-readable router_required / router_exists request
```

`init` never writes routers or calls a model/API and requires no API key.
`--dry-run` remains a compatibility alias for the same inspection behavior.
Inspection respects Git ignores (including ignored tracked files), excludes
obvious secret files, generated directories, symlinks, binaries, and recognizable
credentials, and examines bounded excerpts from at most 600 safe text files.
JSON returns candidate paths rather than source excerpts or generated descriptions;
large repositories may receive partial candidate coverage. Codex must still read
actual files and must not inspect or summarize `.env`, private keys, credentials,
API tokens, or other secrets, or put secrets into routers.

The missing-router hook only injects instructions; it makes no Jev call until
Codex creates routers and explicitly runs routing. It still injects instructions
when the Git GPS executable is unavailable, with a concise diagnostic. Existing
routers use the fast routing path with a 25-second timeout. Hook failures remain
fail-open, allowing Codex to continue the original task.

## Testing

```sh
source .venv/bin/activate
python -m pytest
```
