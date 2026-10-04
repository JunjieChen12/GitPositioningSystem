"""Tests for the command-line interface."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import URLError

from repo_router.cli import main


class InspectCommandTests(unittest.TestCase):
    def run_cli(self, *args):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            status = main(list(args))
        return status, stdout.getvalue(), stderr.getvalue()

    def test_prints_router_contents(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "ROUTER.md").write_text("# Router\n", encoding="utf-8")

            status, stdout, stderr = self.run_cli("inspect", directory)

        self.assertEqual(status, 0)
        self.assertEqual(stdout, "# Router\n")
        self.assertEqual(stderr, "")

    def test_prints_malformed_router_without_parsing(self):
        contents = "## Directories\n- missing description\n"
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "ROUTER.md").write_text(contents, encoding="utf-8")

            status, stdout, stderr = self.run_cli("inspect", directory)

        self.assertEqual((status, stdout, stderr), (0, contents, ""))

    def test_missing_directory_reports_error(self):
        with tempfile.TemporaryDirectory() as directory:
            status, stdout, stderr = self.run_cli("inspect", str(Path(directory) / "missing"))

        self.assertEqual(status, 1)
        self.assertEqual(stdout, "")
        self.assertIn("Repository directory does not exist", stderr)

    def test_missing_router_reports_error(self):
        with tempfile.TemporaryDirectory() as directory:
            status, stdout, stderr = self.run_cli("inspect", directory)

        self.assertEqual(status, 1)
        self.assertEqual(stdout, "")
        self.assertIn("ROUTER.md does not exist", stderr)


class RouteCommandTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        (self.root / "backend" / "auth").mkdir(parents=True)
        (self.root / "ROUTER.md").write_text(
            "## Directories\n- backend/ — JWT backend logic\n", encoding="utf-8"
        )
        (self.root / "backend" / "ROUTER.md").write_text(
            "## Directories\n- auth/ — JWT validation and authentication\n",
            encoding="utf-8",
        )
        (self.root / "backend" / "auth" / "ROUTER.md").write_text(
            "## Important Files\n"
            "- jwt.py — JWT creation and validation\n"
            "- login.py — login endpoints\n",
            encoding="utf-8",
        )
        (self.root / "backend" / "auth" / "jwt.py").touch()

    def run_cli(self, *args):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            status = main(list(args))
        return status, stdout.getvalue(), stderr.getvalue()

    def test_successful_route(self):
        with patch("repo_router.laya.urlopen") as urlopen:
            status, stdout, stderr = self.run_cli(
                "route", str(self.root), "Where is JWT validation handled?"
            )

        self.assertEqual(status, 0)
        self.assertEqual(
            stdout,
            "status: found\npath: backend/auth/jwt.py\nsearch_root: backend/auth\n",
        )
        self.assertEqual(stderr, "")
        urlopen.assert_not_called()

    def test_explicit_keyword_matches_default(self):
        task = "Where is JWT validation handled?"

        default = self.run_cli("route", str(self.root), task)
        explicit = self.run_cli("route", str(self.root), task, "--strategy", "keyword")

        self.assertEqual(explicit, default)

    def test_keyword_trace_shows_each_level(self):
        status, stdout, stderr = self.run_cli(
            "route", str(self.root), "JWT validation", "--trace"
        )

        self.assertEqual(status, 0)
        self.assertEqual(stderr, "")
        self.assertEqual(
            stdout,
            "route trace:\n"
            ". -> backend/\n"
            "backend -> auth/\n"
            "backend/auth -> jwt.py\n"
            "\n"
            "status: found\n"
            "path: backend/auth/jwt.py\n"
            "search_root: backend/auth\n",
        )

    def test_explicit_laya_uses_local_strategy(self):
        response = MagicMock()
        response.read.side_effect = [
            json.dumps({"answers": {"route": {"choice": choice_id}}}).encode()
            for choice_id in ("backend", "auth", "jwt_py")
        ]
        response.__enter__.return_value = response

        with patch("repo_router.laya.urlopen", return_value=response) as urlopen:
            status, stdout, stderr = self.run_cli(
                "route", str(self.root), "Where is JWT validation handled?", "--strategy", "laya"
            )

        self.assertEqual(
            (status, stdout, stderr),
            (0, "status: found\npath: backend/auth/jwt.py\nsearch_root: backend/auth\n", ""),
        )
        self.assertEqual(urlopen.call_count, 3)

    def test_laya_trace_shows_each_level(self):
        response = MagicMock()
        response.read.side_effect = [
            json.dumps(
                {"answers": {"route": {"choice": choice_id, "confidence": 0.9}}}
            ).encode()
            for choice_id in ("backend", "auth", "jwt_py")
        ]
        response.__enter__.return_value = response

        with patch("repo_router.laya.urlopen", return_value=response):
            status, stdout, stderr = self.run_cli(
                "route", str(self.root), "JWT validation", "--strategy", "laya", "--trace"
            )

        self.assertEqual(status, 0)
        self.assertEqual(stderr, "")
        self.assertIn(". -> backend/\nbackend -> auth/\nbackend/auth -> jwt.py\n", stdout)
        self.assertIn("status: found\npath: backend/auth/jwt.py\n", stdout)

    def test_invalid_strategy_is_rejected(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as raised:
                main(["route", str(self.root), "task", "--strategy", "unknown"])

        self.assertEqual(raised.exception.code, 2)
        self.assertIn("invalid choice", stderr.getvalue())

    def test_laya_connection_error_is_reported(self):
        with patch("repo_router.laya.urlopen", side_effect=URLError("connection refused")):
            status, stdout, stderr = self.run_cli(
                "route", str(self.root), "JWT validation", "--strategy", "laya"
            )

        self.assertEqual(status, 1)
        self.assertEqual(stdout, "")
        self.assertTrue(stderr.startswith("git-gps: Cannot connect to Laya"))

    def test_no_match(self):
        status, stdout, stderr = self.run_cli("route", str(self.root), "unrelated topic")

        self.assertEqual((status, stdout, stderr), (0, "status: no_match\nsearch_root: .\n", ""))

    def test_no_match_trace(self):
        status, stdout, stderr = self.run_cli(
            "route", str(self.root), "unrelated topic", "--trace"
        )

        self.assertEqual(
            (status, stdout, stderr),
            (0, "route trace:\n. -> (no match)\n\nstatus: no_match\nsearch_root: .\n", ""),
        )

    def test_missing_nested_router(self):
        (self.root / "backend" / "ROUTER.md").unlink()

        status, stdout, stderr = self.run_cli("route", str(self.root), "JWT validation")

        self.assertEqual(
            (status, stdout, stderr),
            (0, "status: missing_router\nsearch_root: backend\n", ""),
        )

    def test_missing_router_trace(self):
        (self.root / "backend" / "ROUTER.md").unlink()

        status, stdout, stderr = self.run_cli(
            "route", str(self.root), "JWT validation", "--trace"
        )

        self.assertEqual(
            (status, stdout, stderr),
            (0, "route trace:\n. -> backend/\n\nstatus: missing_router\nsearch_root: backend\n", ""),
        )

    def test_missing_target_file(self):
        (self.root / "backend" / "auth" / "jwt.py").unlink()

        status, stdout, stderr = self.run_cli("route", str(self.root), "JWT validation")

        self.assertEqual(
            (status, stdout, stderr),
            (0, "status: missing_target\nsearch_root: backend/auth\n", ""),
        )

    def test_invalid_repository(self):
        status, stdout, stderr = self.run_cli(
            "route", str(self.root / "absent"), "JWT validation"
        )

        self.assertEqual(status, 1)
        self.assertEqual(stdout, "")
        self.assertTrue(stderr.startswith("git-gps: Repository directory does not exist"))
