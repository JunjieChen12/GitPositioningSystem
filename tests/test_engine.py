"""Tests for recursive routing through repository documents."""

import tempfile
import unittest
from pathlib import Path

from repo_router.decision import KeywordRoutingStrategy
from repo_router.engine import RouteResult, RouteStatus, route_repository
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

    def test_no_matching_route_at_root(self):
        result = route_repository(self.root, "unrelated topic", KeywordRoutingStrategy())

        self.assertEqual(result, RouteResult(RouteStatus.NO_MATCH, None, "."))

    def test_no_matching_route_after_descent(self):
        result = route_repository(self.root, "unknown", PathSequenceStrategy("backend/", "missing"))

        self.assertEqual(result, RouteResult(RouteStatus.NO_MATCH, None, "backend"))

    def test_missing_nested_router_preserves_search_directory(self):
        (self.root / "backend" / "ROUTER.md").unlink()

        result = route_repository(self.root, "backend task", PathSequenceStrategy("backend/"))

        self.assertEqual(result, RouteResult(RouteStatus.MISSING_ROUTER, None, "backend"))

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
