"""Tests for parsing ROUTER.md into structured data."""

import unittest

from repo_router.parser import (
    DirectoryEntry,
    FileEntry,
    RouterDocument,
    RouterParseError,
    parse_router,
)


class ParseRouterTests(unittest.TestCase):
    def test_valid_router(self):
        contents = """# Router

## Purpose
Backend application logic.

## Directories
- auth/ — authentication and authorization
- users/ — user account management

## Important Files
- app.py — application entry point
"""

        self.assertEqual(
            parse_router(contents),
            RouterDocument(
                purpose="Backend application logic.",
                directories=[
                    DirectoryEntry("auth/", "authentication and authorization"),
                    DirectoryEntry("users/", "user account management"),
                ],
                files=[FileEntry("app.py", "application entry point")],
            ),
        )

    def test_missing_sections_are_empty(self):
        self.assertEqual(parse_router("# Router\n"), RouterDocument())
        self.assertEqual(
            parse_router("## Purpose\nOnly a purpose.\n"),
            RouterDocument(purpose="Only a purpose."),
        )

    def test_empty_entry_sections(self):
        self.assertEqual(
            parse_router("## Directories\n\n## Important Files\n\n"),
            RouterDocument(),
        )

    def test_unrelated_section_is_ignored(self):
        self.assertEqual(
            parse_router("## Directories\n- src/ — source\n## Notes\nnot an entry\n"),
            RouterDocument(directories=[DirectoryEntry("src/", "source")]),
        )

    def test_malformed_list_entries(self):
        for section, entry in (
            ("Directories", "- auth/"),
            ("Directories", "- — missing path"),
            ("Important Files", "- app.py —"),
            ("Important Files", "app.py — missing bullet"),
        ):
            with self.subTest(section=section, entry=entry):
                with self.assertRaisesRegex(RouterParseError, "line 2"):
                    parse_router(f"## {section}\n{entry}\n")


if __name__ == "__main__":
    unittest.main()
