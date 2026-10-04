"""Tests for recursive routing through repository documents."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from repo_router.decision import KeywordRoutingStrategy
from repo_router.engine import RouteResult, RouteStatus, RouteTraceStep, route_repository
from repo_router.laya import LayaRoutingStrategy
from repo_router.router import RouterError


class PathSequenceStrategy:
    """Choose specified paths to verify that the engine delegates decisions."""

    def __init__(self, *paths):
        self.paths = iter(paths)
        self.calls = []

    def decide(self, task, entries):
        entries = list(entries)
        self.calls.append((task, [entry.path for entry in entries]))
        path = next(self.paths)
        return next((entry for entry in entries if entry.path == path), None)


class RouteRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        (self.root / "backend" / "auth").mkdir(parents=True)
        (self.root / "frontend").mkdir()
        (self.root / "ROUTER.md").write_text(
            "## Directories\n"
            "- backend/ — backend API and business logic\n"
            "- frontend/ — frontend application\n",
            encoding="utf-8",
        )
        (self.root / "backend" / "ROUTER.md").write_text(
            "## Directories\n- auth/ — authentication and authorization\n",
            encoding="utf-8",
        )
        (self.root / "backend" / "auth" / "ROUTER.md").write_text(
            "## Important Files\n"
            "- jwt.py — JWT creation and validation\n"
            "- login.py — login endpoints\n",
            encoding="utf-8",
        )
        (self.root / "frontend" / "ROUTER.md").write_text(
            "## Purpose\nFrontend application.\n", encoding="utf-8"
        )
        (self.root / "backend" / "auth" / "jwt.py").touch()
        (self.root / "backend" / "auth" / "login.py").touch()

    def test_routes_through_nested_documents_to_file(self):
        task = "Where is JWT validation handled?"
        strategy = PathSequenceStrategy("backend/", "auth/", "jwt.py")

        result = route_repository(self.root, task, strategy)

        self.assertEqual(
            result,
            RouteResult(RouteStatus.FOUND, "backend/auth/jwt.py", "backend/auth"),
        )
        self.assertEqual(
            strategy.calls,
            [
                (task, ["backend/", "frontend/"]),
                (task, ["auth/"]),
                (task, ["jwt.py", "login.py"]),
            ],
        )

    def test_routes_with_keyword_strategy_when_each_level_has_matching_words(self):
        result = route_repository(
            self.root, "backend auth JWT validation", KeywordRoutingStrategy()
        )

        self.assertEqual(result.path, "backend/auth/jwt.py")
        self.assertEqual(result.status, RouteStatus.FOUND)
        self.assertEqual(
            result.trace,
            (
                RouteTraceStep(".", "backend/"),
                RouteTraceStep("backend", "auth/"),
                RouteTraceStep("backend/auth", "jwt.py"),
            ),
        )

    def test_laya_trace_preserves_confidence_and_probabilities(self):
        (self.root / "ROUTER.md").write_text(
            "## Important Files\n- target.py — selected file\n", encoding="utf-8"
        )
        (self.root / "target.py").touch()
        response = MagicMock()
        response.read.return_value = json.dumps(
            {
                "answers": {
                    "route": {
                        "choice": "target_py",
                        "confidence": 0.82,
                        "answer_confidence": 0.94,
                        "probabilities": {"target_py": 0.94},
                    }
                }
            }
        ).encode()
        response.__enter__.return_value = response

        with patch("repo_router.laya.urlopen", return_value=response):
            result = route_repository(self.root, "selected file", LayaRoutingStrategy())

        self.assertEqual(result.status, RouteStatus.FOUND)
        self.assertEqual(
            result.trace,
            (
                RouteTraceStep(
                    ".",
                    "target.py",
                    {
                        "confidence": 0.82,
                        "answer_confidence": 0.94,
                        "probabilities": {"target_py": 0.94},
                    },
                ),
            ),
        )

    def test_laya_routes_through_semantic_ids_to_nested_file(self):
        package = self.root / "src" / "repo_router"
        package.mkdir(parents=True)
        (self.root / "ROUTER.md").write_text(
            "## Directories\n- src/ — production source code\n",
            encoding="utf-8",
        )
        (self.root / "src" / "ROUTER.md").write_text(
            "## Directories\n- repo_router/ — routing package\n",
            encoding="utf-8",
        )
        (package / "ROUTER.md").write_text(
            "## Important Files\n- laya.py — local Laya routing integration\n",
            encoding="utf-8",
        )
        (package / "laya.py").touch()
        response = MagicMock()
        response.read.side_effect = [
            json.dumps({"answers": {"route": {"choice": choice_id}}}).encode()
            for choice_id in ("src", "repo_router", "laya_py")
        ]
        response.__enter__.return_value = response

        with patch("repo_router.laya.urlopen", return_value=response) as urlopen:
            result = route_repository(self.root, "Find the Laya integration", LayaRoutingStrategy())

        self.assertEqual(result.status, RouteStatus.FOUND)
        self.assertEqual(result.path, "src/repo_router/laya.py")
        self.assertEqual(
            result.trace,
            (
                RouteTraceStep(".", "src/"),
                RouteTraceStep("src", "repo_router/"),
                RouteTraceStep("src/repo_router", "laya.py"),
            ),
        )
        self.assertEqual(urlopen.call_count, 3)

    def test_no_matching_route_at_root(self):
        result = route_repository(self.root, "unrelated topic", KeywordRoutingStrategy())

        self.assertEqual(result, RouteResult(RouteStatus.NO_MATCH, None, "."))
        self.assertEqual(result.trace, (RouteTraceStep(".", None),))

    def test_no_matching_route_after_descent(self):
        result = route_repository(self.root, "unknown", PathSequenceStrategy("backend/", "missing"))

        self.assertEqual(result, RouteResult(RouteStatus.NO_MATCH, None, "backend"))

    def test_missing_nested_router_preserves_search_directory(self):
        (self.root / "backend" / "ROUTER.md").unlink()

        result = route_repository(self.root, "backend task", PathSequenceStrategy("backend/"))

        self.assertEqual(result, RouteResult(RouteStatus.MISSING_ROUTER, None, "backend"))
        self.assertEqual(result.trace, (RouteTraceStep(".", "backend/"),))

    def test_missing_target_preserves_search_directory(self):
        (self.root / "backend" / "auth" / "jwt.py").unlink()

        result = route_repository(
            self.root, "backend auth JWT validation", KeywordRoutingStrategy()
        )

        self.assertEqual(result, RouteResult(RouteStatus.MISSING_TARGET, None, "backend/auth"))

    def test_invalid_repository(self):
        with self.assertRaisesRegex(RouterError, "Repository directory does not exist"):
            route_repository(self.root / "absent", "task", KeywordRoutingStrategy())

        (self.root / "ROUTER.md").unlink()
        with self.assertRaisesRegex(RouterError, "ROUTER.md does not exist"):
            route_repository(self.root, "task", KeywordRoutingStrategy())

    def test_maximum_depth_preserves_next_search_directory(self):
        result = route_repository(
            self.root, "task", PathSequenceStrategy("backend/", "auth/"), max_depth=1
        )

        self.assertEqual(result, RouteResult(RouteStatus.MAX_DEPTH, None, "backend/auth"))

    def test_revisited_directory_stops(self):
        (self.root / "backend" / "ROUTER.md").write_text(
            "## Directories\n- ../ — repository root\n", encoding="utf-8"
        )

        result = route_repository(self.root, "task", PathSequenceStrategy("backend/", "../"))

        self.assertEqual(result, RouteResult(RouteStatus.CYCLE, None, "backend"))

    def test_route_cannot_leave_repository(self):
        (self.root / "ROUTER.md").write_text(
            "## Important Files\n- ../outside.py — outside repository\n", encoding="utf-8"
        )

        with self.assertRaisesRegex(RouterError, "Route path leaves repository"):
            route_repository(self.root, "task", PathSequenceStrategy("../outside.py"))

    def test_negative_max_depth_is_invalid(self):
        with self.assertRaisesRegex(ValueError, "max_depth"):
            route_repository(self.root, "task", KeywordRoutingStrategy(), max_depth=-1)

    def test_missing_file_target(self):
        (self.root / "backend" / "auth" / "jwt.py").unlink()

        result = route_repository(
            self.root,
            "task",
            PathSequenceStrategy("backend/", "auth/", "jwt.py"),
        )

        self.assertEqual(
            result,
            RouteResult(
                RouteStatus.MISSING_TARGET,
                None,
                "backend/auth",
            ),
        )


if __name__ == "__main__":
    unittest.main()
