# Git GPS

Git GPS is a repository-routing layer for AI coding agents. Agents often search large portions of a repository before finding relevant code. Git GPS uses hierarchical `ROUTER.md` files and a choice of routing strategies to narrow the search space before broader code search.

## How It Works

```text
User task
  ↓
ROUTER.md
  ↓
routing strategy
  ↓
relevant directory
  ↓
nested ROUTER.md
  ↓
relevant file or smaller search root
  ↓
coding agent
```

Git GPS starts at the repository root, chooses a listed directory or file, and follows directories through nested routers. A selected file becomes the final path. If routing stops early, the result includes a `search_root` where an agent can continue searching. Strategies choose only from entries in the current `ROUTER.md`; Git GPS does not generate routers automatically.

| Strategy | Where it runs | What to expect |
| --- | --- | --- |
| `keyword` (default) | Locally, without a model | Fastest; uses word overlap between the task and entry paths/descriptions. It can miss related wording that shares no keywords. |
| `laya` | Local Laya server | Semantic routing beyond exact word matches; slower than keyword because it runs local model inference. No hosted API is needed. |
| `jev` | OpenRouter Decisions API | Hosted semantic routing; needs an API key but no local model server. It may incur API cost and is often faster than local Laya, depending on hardware and network conditions. |

## Installation

Requires Python 3.10 or newer. Clone the repository (replace `<repo-url>` with its actual clone URL):

**macOS/Linux**

```sh
git clone "<repo-url>" GitPositioningSystem
cd GitPositioningSystem
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
```

**Windows PowerShell**

```powershell
git clone "<repo-url>" GitPositioningSystem
cd GitPositioningSystem
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
```

Check that the installed command is available:

```sh
git-gps --help
git-gps inspect .
```

`inspect` prints the root `ROUTER.md` as written. The `route` command accepts `git-gps route REPOSITORY TASK [--strategy keyword|laya|jev] [--trace]`.

## ROUTER.md Format

Add a `ROUTER.md` at the target repository root. Directories can contain their own `ROUTER.md` files for deeper routing. The parser recognizes these headings and list entries:

```markdown
# Router

## Purpose
Short description.

## Directories
- src/ — production source code
- tests/ — automated tests

## Important Files
- README.md — project documentation
- pyproject.toml — Python project configuration
```

Use an **em dash (`—`) with spaces** between each path and description. Describe entries in terms a task might use. Directory paths are resolved relative to the router that lists them.

For example:

```text
repo/
├── ROUTER.md
└── src/
    ├── ROUTER.md
    └── repo_router/
        ├── ROUTER.md
        └── laya.py
```

A route can follow `root ROUTER.md → src/ → src/ROUTER.md → repo_router/ → src/repo_router/ROUTER.md → laya.py`. If a selected directory has no nested router, Git GPS reports that directory as a fallback `search_root`.

## Keyword Routing

Keyword is the default and needs no server or API key:

```sh
git-gps route . "Where is recursive routing implemented?"
git-gps route . "Where is recursive routing implemented?" --strategy keyword
git-gps route . "Where is recursive routing implemented?" --strategy keyword --trace
```

Keyword routing is extremely fast, but it may return `no_match` when the task and router descriptions use different words.

## Laya Setup

Install the optional local server in the active environment:

```sh
python -m pip install "laya[serve]"
laya-serve
```

Keep the server running. In another terminal, go to the Git GPS checkout, activate its environment, and verify the server:

```sh
cd GitPositioningSystem
source .venv/bin/activate
curl http://localhost:8000/health
git-gps route . "Where is the Laya integration handled?" --strategy laya --trace
```

Git GPS uses these settings:

| Variable | Default | Purpose |
| --- | --- | --- |
| `LAYA_BASE_URL` | `http://localhost:8000` | URL of the local Laya server. |
| `LAYA_ENDPOINT_PATH` | `/v1/systemone` | Decision endpoint path on that server. |
| `LAYA_DEVICE` | Automatic selection by Laya | Server-side inference device; set it before starting `laya-serve`. |

Laya can use CPU, Apple MPS, or NVIDIA CUDA when its installation and hardware support them. On macOS with a suitable PyTorch backend, Laya may select MPS automatically. To request CUDA on Windows PowerShell:

```powershell
$env:LAYA_DEVICE="cuda"
laya-serve
```

Check whether PyTorch sees a CUDA GPU:

```sh
python -c "import torch; print(torch.cuda.is_available()); print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'NO CUDA')"
```

CUDA is not guaranteed to be available. Check the server's `/health` response after it loads a model: its reported device shows where inference actually ran and may be `cpu` if GPU use failed. See the [Laya server documentation](https://github.com/NandhaKishorM/laya/blob/main/docs/http-api.md) for its health fields.

## Jev/OpenRouter Setup

Jev calls the hosted [OpenRouter Decisions API](https://openrouter.ai/blog/tutorials/how-to-use-jev/) over the internet. It does not require `laya-serve`. Set your own API key in the terminal that runs Git GPS:

**macOS/Linux**

```sh
export OPENROUTER_API_KEY="sk-or-v1-..."
```

**Windows PowerShell**

```powershell
$env:OPENROUTER_API_KEY="sk-or-v1-..."
```

The defaults are `JEV_BASE_URL=https://openrouter.ai/api/alpha/decisions` and `JEV_MODEL=typesafe/jev-1.13`. Override them only if needed:

```sh
export JEV_BASE_URL="https://openrouter.ai/api/alpha/decisions"
export JEV_MODEL="typesafe/jev-1.13"
```

```powershell
$env:JEV_BASE_URL="https://openrouter.ai/api/alpha/decisions"
$env:JEV_MODEL="typesafe/jev-1.13"
```

```sh
git-gps route . "Where is the Laya integration handled?" --strategy jev --trace
```

OpenRouter usage may cost money. Git GPS also accepts optional `OPENROUTER_HTTP_REFERER` and `OPENROUTER_APP_TITLE` environment variables for the corresponding OpenRouter request headers; neither is required.

## Trace

Add `--trace` to any `route` command to see each decision. For example, a semantic route might print:

```text
route trace:
. -> src/
src -> repo_router/
src/repo_router -> laya.py

status: found
path: src/repo_router/laya.py
search_root: src/repo_router
```

Each arrow shows the current directory and selected entry. Trace helps debug routing and compare strategies. A keyword route may instead stop at the root:

```text
route trace:
. -> (no match)

status: no_match
search_root: .
```

Without `--trace`, Git GPS prints only the status and result fields. Laya and Jev decision metadata, including confidence or probabilities when supplied, is retained in the structured Python route trace for analysis; the CLI trace shows the selected entries.

For programmatic routing, pass a strategy to the engine:

```python
from repo_router.engine import route_repository
from repo_router.laya import LayaRoutingStrategy

result = route_repository(".", "Where is the Laya integration handled?", LayaRoutingStrategy())
```

## Using Git GPS With Another Project

Git GPS does not need to live inside the target project. Install it once, activate that environment, then run it in a project with compatible `ROUTER.md` files:

```sh
source ~/gitgps/.venv/bin/activate  # adjust to your Git GPS checkout
cd ~/my-other-project
git-gps route . "Where is note creation handled?" --strategy laya --trace
```

For Laya, keep `laya-serve` running. For Jev, set `OPENROUTER_API_KEY`. To create routers in a new project, start with a root file listing the main directories, then add nested files only where a narrower choice is useful.

## Using Git GPS With Codex

Integration with coding agents is currently manual:

1. Run `git-gps route . "Where is note creation handled?" --strategy jev --trace` in the target project.
2. Take the returned `path` or `search_root`.
3. Tell Codex: “Git GPS routed this task to `<path>`. Start there and expand to related files if needed. Search more broadly if the route is insufficient.”

## Testing

```sh
python -m pip install pytest
pytest
```

Unit tests mock Laya and Jev HTTP responses. Normal `pytest` runs need neither a live Laya server nor an OpenRouter key and make no live Laya or Jev requests. Run live CLI checks separately when those services are configured:

```sh
git-gps route . "Where is the Laya integration handled?" --strategy keyword --trace
git-gps route . "Where is the Laya integration handled?" --strategy laya --trace
git-gps route . "Where is the Laya integration handled?" --strategy jev --trace
```

The second command needs `laya-serve`; the third needs `OPENROUTER_API_KEY`.

## Benchmarking

Compare the same task with `--strategy keyword`, `--strategy laya`, and `--strategy jev`. Record routing accuracy, latency, routing levels, final path or `search_root`, available confidence/probabilities, and whether broader search was needed. One local Windows run with an RTX 4070 Ti measured about 0.12 seconds for keyword routing (no match) and 6.34 seconds for Laya (correct route). Those numbers are a single hardware-dependent example, not a general performance guarantee.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| `git-gps: command not found` | Activate the intended virtual environment and run `python -m pip install -e .` from the Git GPS checkout. |
| Laya connection refused | Start `laya-serve`, then check `curl http://localhost:8000/health` and `LAYA_BASE_URL`. |
| Laya uses CPU | Check the PyTorch GPU backend, request `LAYA_DEVICE=cuda` if supported, and inspect `/health` after a model loads. |
| `OPENROUTER_API_KEY` missing | Export or set it in the same shell that runs `git-gps`. |
| OpenRouter HTTP 401 | Verify the key independently against OpenRouter, then create a fresh key if needed. |
| `SSL CERTIFICATE_VERIFY_FAILED` on macOS Python | Install or repair the CA certificates for your Python installation (for python.org Python, run its *Install Certificates.command*). Keep TLS verification enabled. |

## Security

Keep API keys in environment variables. Do not commit or hardcode them, or paste real secrets into `README.md` or router files.

## Future Work

Current features are recursive routing, keyword routing, local Laya, hosted Jev, and trace output. Possible future work includes automated benchmarking, Codex/Claude integration, hybrid or fallback routing, router generation, and confidence-aware strategy selection.
