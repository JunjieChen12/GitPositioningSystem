"""Tests for the local Laya routing strategy without a server."""

import json
import re
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

from repo_router.decision import RoutingStrategy
from repo_router.laya import (
    LayaConnectionError,
    LayaResponseError,
    LayaRoutingStrategy,
)
from repo_router.parser import DirectoryEntry, FileEntry


class LayaRoutingStrategyTests(unittest.TestCase):
    def setUp(self):
        self.entries = [
            DirectoryEntry("auth/", "authentication and authorization"),
            FileEntry("login.py", "login endpoints"),
        ]
        self.strategy: RoutingStrategy = LayaRoutingStrategy()

    def mock_response(self, body):
        response = MagicMock()
        response.read.return_value = body
        response.__enter__.return_value = response
        return patch("repo_router.laya.urlopen", return_value=response)

    def test_chooses_directory_and_sends_all_entry_fields(self):
        body = json.dumps({"answers": {"route": {"choice": "auth"}}}).encode()
        with self.mock_response(body) as urlopen:
            chosen = self.strategy.decide("Fix users randomly being logged out", self.entries)

        self.assertIs(chosen, self.entries[0])
        request = urlopen.call_args.args[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.full_url, "http://localhost:8000/v1/systemone")
        self.assertEqual(request.get_header("Content-type"), "application/json")
        payload = json.loads(request.data)
        self.assertEqual(payload["state"]["body"], "Fix users randomly being logged out")
        self.assertEqual(
            payload["questions"]["route"]["criteria"],
            {
                "auth": "directory auth/: authentication and authorization",
                "login_py": "file login.py: login endpoints",
            },
        )
        self.assertEqual(set(payload["questions"]["route"]["criteria"]), {"auth", "login_py"})

    def test_chooses_file_and_preserves_original_object(self):
        body = json.dumps({"answers": {"route": {"choice": "login_py"}}}).encode()
        with self.mock_response(body):
            chosen = self.strategy.decide("Find login endpoints", iter(self.entries))

        self.assertIs(chosen, self.entries[1])

    def test_semantic_ids_from_directory_and_file_paths(self):
        entries = [
            DirectoryEntry("src/", "production source code"),
            DirectoryEntry("tests/", "automated tests"),
            DirectoryEntry("docs/", "documentation"),
            FileEntry("laya.py", "Laya integration"),
            FileEntry("engine.py", "routing engine"),
            DirectoryEntry("repo_router/", "router package"),
        ]
        body = json.dumps({"answers": {"route": {"choice": "laya_py"}}}).encode()
        with self.mock_response(body) as urlopen:
            chosen = self.strategy.decide("Laya integration", entries)

        self.assertIs(chosen, entries[3])
        criteria = json.loads(urlopen.call_args.args[0].data)["questions"]["route"]["criteria"]
        self.assertEqual(
            list(criteria), ["src", "tests", "docs", "laya_py", "engine_py", "repo_router"]
        )

    def test_colliding_paths_receive_deterministic_suffixes(self):
        entries = [
            FileEntry("foo.bar", "first"),
            FileEntry("foo-bar", "second"),
        ]
        body = json.dumps({"answers": {"route": {"choice": "foo_bar_2"}}}).encode()
        with self.mock_response(body) as urlopen:
            chosen = self.strategy.decide("second", entries)

        self.assertIs(chosen, entries[1])
        criteria = json.loads(urlopen.call_args.args[0].data)["questions"]["route"]["criteria"]
        self.assertEqual(list(criteria), ["foo_bar", "foo_bar_2"])

    def test_collision_suffix_does_not_claim_another_paths_base_id(self):
        entries = [
            FileEntry("foo.bar", "first"),
            FileEntry("foo-bar", "second"),
            FileEntry("foo_bar_2", "third"),
            FileEntry("foo/bar", "fourth"),
        ]
        body = json.dumps({"answers": {"route": {"choice": "foo_bar_4"}}}).encode()
        with self.mock_response(body) as urlopen:
            chosen = self.strategy.decide("fourth", entries)

        self.assertIs(chosen, entries[3])
        criteria = json.loads(urlopen.call_args.args[0].data)["questions"]["route"]["criteria"]
        self.assertEqual(list(criteria), ["foo_bar", "foo_bar_3", "foo_bar_2", "foo_bar_4"])

    def test_unusual_paths_produce_safe_choice_keys(self):
        entries = [
            FileEntry("../@Strange Name!.py", "odd file"),
            FileEntry("123.py", "numeric filename"),
            DirectoryEntry("./", "current directory"),
        ]
        body = json.dumps({"answers": {"route": {"choice": "strange_name_py"}}}).encode()
        with self.mock_response(body) as urlopen:
            chosen = self.strategy.decide("odd file", entries)

        self.assertIs(chosen, entries[0])
        criteria = json.loads(urlopen.call_args.args[0].data)["questions"]["route"]["criteria"]
        self.assertEqual(list(criteria), ["strange_name_py", "path_123_py", "path_2e2f"])
        self.assertTrue(all(re.fullmatch(r"[a-z][a-z0-9_]*", key) for key in criteria))

    def test_preserves_metadata_for_last_decision_and_clears_it_next_time(self):
        body = json.dumps(
            {
                "answers": {
                    "route": {
                        "choice": "login_py",
                        "confidence": 0.7,
                        "answer_confidence": 0.85,
                        "probabilities": {"auth": 0.15, "login_py": 0.85},
                    }
                }
            }
        ).encode()
        strategy = LayaRoutingStrategy()
        with self.mock_response(body):
            strategy.decide("Find login endpoints", self.entries)

        self.assertEqual(
            strategy.decision_metadata(),
            {
                "confidence": 0.7,
                "answer_confidence": 0.85,
                "probabilities": {"auth": 0.15, "login_py": 0.85},
            },
        )
        self.assertIsNone(strategy.decide("task", []))
        self.assertIsNone(strategy.decision_metadata())

    def test_unknown_id_raises_response_error(self):
        body = json.dumps({"answers": {"route": {"choice": "invented.py"}}}).encode()
        with self.mock_response(body):
            with self.assertRaisesRegex(LayaResponseError, "unknown route ID"):
                self.strategy.decide("task", self.entries)

    def test_malformed_json_raises_response_error(self):
        with self.mock_response(b"not json"):
            with self.assertRaisesRegex(LayaResponseError, "malformed JSON"):
                self.strategy.decide("task", self.entries)

    def test_malformed_shape_raises_response_error(self):
        for body in (b"[]", b"{}", b'{"answers": {"route": {}}}'):
            with self.subTest(body=body), self.mock_response(body):
                with self.assertRaises(LayaResponseError):
                    self.strategy.decide("task", self.entries)

    def test_empty_response_raises_response_error(self):
        with self.mock_response(b""):
            with self.assertRaisesRegex(LayaResponseError, "empty response"):
                self.strategy.decide("task", self.entries)

    def test_explicit_no_choice_returns_none(self):
        body = json.dumps({"answers": {"route": {"choice": None}}}).encode()
        with self.mock_response(body):
            self.assertIsNone(self.strategy.decide("task", self.entries))

    def test_empty_entries_returns_none_without_request(self):
        with patch("repo_router.laya.urlopen") as urlopen:
            self.assertIsNone(self.strategy.decide("task", []))
        urlopen.assert_not_called()

    def test_connection_failure_raises_specific_error(self):
        with patch("repo_router.laya.urlopen", side_effect=URLError("connection refused")):
            with self.assertRaisesRegex(LayaConnectionError, "Cannot connect to Laya"):
                self.strategy.decide("task", self.entries)

    def test_http_error_raises_response_error(self):
        error = HTTPError("http://localhost:8000/v1/systemone", 503, "busy", None, None)
        with patch("repo_router.laya.urlopen", side_effect=error):
            with self.assertRaisesRegex(LayaResponseError, "HTTP 503"):
                self.strategy.decide("task", self.entries)

    def test_configured_base_url_and_path_are_respected(self):
        body = json.dumps({"answers": {"route": {"choice": "auth"}}}).encode()
        with patch.dict(
            "os.environ",
            {"LAYA_BASE_URL": "http://127.0.0.1:9000/local/", "LAYA_ENDPOINT_PATH": "/choose"},
        ):
            strategy = LayaRoutingStrategy()
        with self.mock_response(body) as urlopen:
            strategy.decide("task", self.entries)

        self.assertEqual(urlopen.call_args.args[0].full_url, "http://127.0.0.1:9000/local/choose")


if __name__ == "__main__":
    unittest.main()
