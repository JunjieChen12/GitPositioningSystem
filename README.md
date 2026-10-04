# Git-GPS

Repo Router is a routing layer for AI coding agents such as Codex and Claude.

AI coding agents often need to search through an entire repository to understand
where relevant code is located. This can require many file searches and can
introduce unnecessary latency, token usage, and blind spots.

Repo Router uses hierarchical router documents placed throughout a repository
to help an AI agent narrow down which directories and files are most relevant
to a task before performing normal code search.

## Basic Flow

User Task
↓
Root Router Document
↓
Routing Decision
↓
Relevant Directory
↓
Directory Router Document
↓
Relevant Files
↓
Coding Agent

The CLI uses keyword matching by default and can use a local Laya server for
routing decisions.

## Inspect a root router document

Install the CLI with `python3 -m pip install -e .`, then run:

```sh
git-gps inspect /path/to/repository
```

The command prints that directory's `ROUTER.md`. If the directory or document
is missing, it prints an error and exits with a nonzero status.

## Route a task

Use a task description to follow matching entries through nested router documents:

```sh
git-gps route . "Where is JWT validation handled?"
git-gps route . "Where is auth handled?" --strategy keyword
git-gps route . "Where is auth handled?" --strategy laya
git-gps route . "Where is auth handled?" --strategy laya --trace
```

The command prints a status and a `search_root` directory for broader search.
When it finds a file, it also prints its repository-relative `path`. Routing
uses case-insensitive keyword matches by default, so the task needs matching
words in the entries at each level. Select `--strategy laya` to ask a local
Laya server to choose among the listed entries. Add `--trace` to print each
directory and entry selected during traversal. Laya confidence and choice
probabilities are also retained in the structured route trace for Python callers.

## Use a local Laya server from Python

`LayaRoutingStrategy` sends the task and available route entries to a local
Laya choice endpoint. It returns only an entry from that list. The default
server URL is `http://localhost:8000`; set `LAYA_BASE_URL` to change it and
`LAYA_ENDPOINT_PATH` to change the default `/v1/systemone` path.

```python
from repo_router.engine import route_repository
from repo_router.laya import LayaRoutingStrategy

result = route_repository(".", "Where is JWT validation handled?", LayaRoutingStrategy())
```

The local server must be running when this strategy is used. Connection and
response errors are reported as Laya-specific exceptions.

## Goals

- Reduce unnecessary repository searches
- Reduce files inspected by coding agents
- Reduce token usage
- Reduce time required to locate relevant code
- Maintain high recall of relevant files
- Allow fallback to normal repository search when routing is uncertain
