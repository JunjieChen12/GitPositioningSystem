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
