# Repo Router

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

Eventually, Jev/Laya will be used to make routing decisions from natural
language router documents.

## Inspect a root router document

Install the CLI with `python3 -m pip install -e .`, then run:

```sh
git-gps inspect /path/to/repository
```

The command prints that directory's `ROUTER.md`. If the directory or document
is missing, it prints an error and exits with a nonzero status.

## Goals

- Reduce unnecessary repository searches
- Reduce files inspected by coding agents
- Reduce token usage
- Reduce time required to locate relevant code
- Maintain high recall of relevant files
- Allow fallback to normal repository search when routing is uncertain
