"""Tests for selecting a route entry from a user task."""

import unittest

from repo_router.decision import KeywordRoutingStrategy, RoutingStrategy
from repo_router.parser import DirectoryEntry, FileEntry


class KeywordRoutingStrategyTests(unittest.TestCase):
    def setUp(self):
        self.strategy: RoutingStrategy = KeywordRoutingStrategy()

    def test_exact_path_keyword_match(self):
        entries = [
            DirectoryEntry("src/", "main application code"),
            DirectoryEntry("tests/", "automated checks"),
        ]

        self.assertIs(self.strategy.decide("Find the tests", entries), entries[1])

    def test_description_match(self):
        entries = [
            DirectoryEntry("src/", "main application code"),
            DirectoryEntry("tests/", "automated tests"),
            DirectoryEntry("docs/", "project documentation"),
        ]

        self.assertIs(self.strategy.decide("Where are the automated tests?", entries), entries[1])

    def test_case_insensitive_file_match(self):
        entries = [
            FileEntry("README.md", "project overview"),
            FileEntry("APP.py", "application entry point"),
        ]

        self.assertIs(self.strategy.decide("Find the app ENTRY point", entries), entries[1])

    def test_no_meaningful_match(self):
        entries = [DirectoryEntry("src/", "main application code")]

        self.assertIsNone(self.strategy.decide("Where are the tests?", entries))
        self.assertIsNone(self.strategy.decide("Where is it?", entries))
        self.assertIsNone(self.strategy.decide("tests", []))

    def test_tie_keeps_first_entry(self):
        entries = [
            DirectoryEntry("alpha/", "shared code"),
            DirectoryEntry("beta/", "shared code"),
        ]

        self.assertIs(self.strategy.decide("shared", entries), entries[0])

    def test_path_match_outweighs_description_match(self):
        entries = [
            DirectoryEntry("src/", "tests"),
            DirectoryEntry("tests/", "source code"),
        ]

        self.assertIs(self.strategy.decide("tests", entries), entries[1])


if __name__ == "__main__":
    unittest.main()
