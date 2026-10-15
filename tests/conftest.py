"""Tests must explicitly mock hosted calls; accidental paid requests fail locally."""

from unittest.mock import Mock

import pytest


@pytest.fixture(autouse=True)
def block_unmocked_model_requests(monkeypatch):
    for module in ("repo_router.jev", "repo_router.laya", "repo_router.generation"):
        monkeypatch.setattr(f"{module}.urlopen", Mock(side_effect=AssertionError(
            "Network calls must be explicitly mocked in tests")))
