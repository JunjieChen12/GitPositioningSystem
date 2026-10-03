"""Tests for reading a repository's root router document."""

import tempfile
import unittest
from pathlib import Path

from repo_router.router import RouterError, read_root_router


class ReadRootRouterTests(unittest.TestCase):
    def test_reads_root_router_contents(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "ROUTER.md").write_text("# Router\n\nHello.\n", encoding="utf-8")

            self.assertEqual(read_root_router(root), "# Router\n\nHello.\n")

    def test_missing_repository_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing"

            with self.assertRaisesRegex(RouterError, "Repository directory does not exist"):
                read_root_router(missing)

    def test_missing_router_document(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RouterError, "ROUTER.md does not exist"):
                read_root_router(directory)

    def test_router_is_a_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "ROUTER.md").mkdir()

            with self.assertRaisesRegex(RouterError, "ROUTER.md does not exist"):
                read_root_router(directory)
