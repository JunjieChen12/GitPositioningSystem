# Router

## Purpose
Contains the core Git Positioning System implementation.

## Directories

## Important Files
- cli.py — handles command-line arguments including selecting keyword or Laya routing strategies
- engine.py — performs recursive hierarchical traversal through ROUTER.md files
- decision.py — defines the RoutingStrategy interface and keyword-based routing
- laya.py — implements LayaRoutingStrategy and communicates with the local Laya /v1/systemone API
- jev.py — implements JevRoutingStrategy through OpenRouter's Decisions API
- route_ids.py — creates shared semantic route choice IDs from entry paths
- parser.py — parses ROUTER.md text into directory and file entries
- router.py — reads and validates ROUTER.md files from the filesystem
