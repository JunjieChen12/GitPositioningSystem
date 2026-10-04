"""Tests for the command-line interface."""

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

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
        status, stdout, stderr = self.run_cli(
            "route", str(self.root), "Where is JWT validation handled?"
        )

        self.assertEqual(status, 0)
        self.assertEqual(
            stdout,
            "status: found\npath: backend/auth/jwt.py\nsearch_root: backend/auth\n",
        )
        self.assertEqual(stderr, "")

    def test_no_match(self):
        status, stdout, stderr = self.run_cli("route", str(self.root), "unrelated topic")

        self.assertEqual((status, stdout, stderr), (0, "status: no_match\nsearch_root: .\n", ""))

    def test_missing_nested_router(self):
        (self.root / "backend" / "ROUTER.md").unlink()

        status, stdout, stderr = self.run_cli("route", str(self.root), "JWT validation")

        self.assertEqual(
            (status, stdout, stderr),
            (0, "status: missing_router\nsearch_root: backend\n", ""),
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
