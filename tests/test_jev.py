"""Tests for hosted Jev routing with mocked OpenRouter responses."""

import io
import json
import os
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

from repo_router.jev import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    JevConfigurationError,
    JevConnectionError,
    JevResponseError,
    JevRoutingStrategy,
)
from repo_router.parser import DirectoryEntry, FileEntry


class JevRoutingStrategyTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        self.strategy = JevRoutingStrategy()
        self.entries = [
            DirectoryEntry("src/", "production source code"),
            DirectoryEntry("tests/", "automated tests"),
            FileEntry("laya.py", "local Laya routing integration"),
        ]

    def mock_response(self, data):
        response = MagicMock()
        response.read.return_value = data
        response.__enter__.return_value = response
        return patch("repo_router.jev.urlopen", return_value=response)

    def test_sends_semantic_ids_and_maps_directory_to_original_object(self):
        body = json.dumps({"answers": {"route": {"choice": "src"}}}).encode()
        task = "Where is the Laya integration handled?"
        with self.mock_response(body) as urlopen:
            chosen = self.strategy.decide(task, self.entries)

        self.assertIs(chosen, self.entries[0])
        request = urlopen.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(payload["model"], "typesafe/jev-1.13")
        self.assertEqual(payload["state"], task)
        self.assertNotIsInstance(payload["state"], dict)
        self.assertEqual(payload["questions"]["route"]["type"], "choice")
        self.assertEqual(
            payload["questions"]["route"]["criteria"],
            {
                "src": "directory src/: production source code",
                "tests": "directory tests/: automated tests",
                "laya_py": "file laya.py: local Laya routing integration",
            },
        )

    def test_maps_file_id_to_original_object(self):
        body = json.dumps({"answers": {"route": {"choice": "laya_py"}}}).encode()
        with self.mock_response(body):
            chosen = self.strategy.decide("Find Laya", iter(self.entries))

        self.assertIs(chosen, self.entries[2])

    def test_default_endpoint_model_and_authentication_headers(self):
        body = json.dumps({"answers": {"route": {"choice": "src"}}}).encode()
        with self.mock_response(body) as urlopen:
            self.strategy.decide("Find source", self.entries)

        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, DEFAULT_BASE_URL)
        self.assertEqual(request.full_url, "https://openrouter.ai/api/alpha/decisions")
        self.assertEqual(json.loads(request.data)["model"], DEFAULT_MODEL)
        self.assertEqual(DEFAULT_MODEL, "typesafe/jev-1.13")
        self.assertEqual(request.get_header("Authorization"), "Bearer test-key")
        self.assertEqual(request.get_header("Content-type"), "application/json")
        header_names = {name.lower() for name, _ in request.header_items()}
        self.assertNotIn("http-referer", header_names)
        self.assertNotIn("x-openrouter-title", header_names)

    def test_optional_attribution_headers_are_only_sent_when_configured(self):
        with patch.dict(
            os.environ,
            {
                "OPENROUTER_HTTP_REFERER": "https://example.test/git-gps",
                "OPENROUTER_APP_TITLE": "Git GPS",
            },
        ):
            strategy = JevRoutingStrategy()
        body = json.dumps({"answers": {"route": {"choice": "src"}}}).encode()
        with self.mock_response(body) as urlopen:
            strategy.decide("Find source", self.entries)

        headers = {name.lower(): value for name, value in urlopen.call_args.args[0].header_items()}
        self.assertEqual(headers["http-referer"], "https://example.test/git-gps")
        self.assertEqual(headers["x-openrouter-title"], "Git GPS")

    def test_configured_endpoint_and_model_are_used(self):
        with patch.dict(
            os.environ,
            {"JEV_BASE_URL": "http://localhost:9999/decisions", "JEV_MODEL": "custom/jev"},
        ):
            strategy = JevRoutingStrategy()
        body = json.dumps({"answers": {"route": {"choice": "src"}}}).encode()
        with self.mock_response(body) as urlopen:
            strategy.decide("Find source", self.entries)

        self.assertEqual(urlopen.call_args.args[0].full_url, "http://localhost:9999/decisions")
        self.assertEqual(json.loads(urlopen.call_args.args[0].data)["model"], "custom/jev")

    def test_missing_api_key_raises_before_http_request(self):
        with patch.dict(os.environ, {}, clear=True):
            strategy = JevRoutingStrategy()
        with patch("repo_router.jev.urlopen") as urlopen:
            with self.assertRaisesRegex(JevConfigurationError, "OPENROUTER_API_KEY"):
                strategy.decide("task", self.entries)

        urlopen.assert_not_called()

    def test_empty_entries_return_none_without_http_request(self):
        with patch("repo_router.jev.urlopen") as urlopen:
            self.assertIsNone(self.strategy.decide("task", []))
        urlopen.assert_not_called()

    def test_colliding_semantic_ids_are_unique_and_map_back(self):
        entries = [FileEntry("foo-bar.py", "first"), FileEntry("foo_bar.py", "second")]
        body = json.dumps({"answers": {"route": {"choice": "foo_bar_py_2"}}}).encode()
        with self.mock_response(body) as urlopen:
            chosen = self.strategy.decide("second", entries)

        self.assertIs(chosen, entries[1])
        criteria = json.loads(urlopen.call_args.args[0].data)["questions"]["route"]["criteria"]
        self.assertEqual(list(criteria), ["foo_bar_py", "foo_bar_py_2"])

    def test_unknown_semantic_id_raises_response_error(self):
        body = json.dumps({"answers": {"route": {"choice": "does_not_exist"}}}).encode()
        with self.mock_response(body):
            with self.assertRaisesRegex(JevResponseError, "unknown route ID"):
                self.strategy.decide("task", self.entries)

    def test_malformed_json_raises_response_error(self):
        with self.mock_response(b"not json"):
            with self.assertRaisesRegex(JevResponseError, "malformed Jev JSON"):
                self.strategy.decide("task", self.entries)

    def test_empty_response_raises_response_error(self):
        with self.mock_response(b""):
            with self.assertRaisesRegex(JevResponseError, "empty Jev response"):
                self.strategy.decide("task", self.entries)

    def test_missing_route_answer_raises_response_error(self):
        for body in (
            b"{}",
            b'{"answers": {}}',
            b'{"answers": {"route": {}}}',
            b'{"answers": {"route": {"choice": null}}}',
        ):
            with self.subTest(body=body), self.mock_response(body):
                with self.assertRaisesRegex(JevResponseError, "no route answer"):
                    self.strategy.decide("task", self.entries)

    def test_http_error_includes_response_body_without_exposing_api_key(self):
        error_body = b'{"error":{"message":"User not found.","code":401},"token":"test-key"}'
        error = HTTPError(DEFAULT_BASE_URL, 401, "unauthorized", None, io.BytesIO(error_body))
        with patch("repo_router.jev.urlopen", side_effect=error):
            with self.assertRaises(JevResponseError) as raised:
                self.strategy.decide("task", self.entries)

        self.assertIn("HTTP 401", str(raised.exception))
        self.assertIn("User not found.", str(raised.exception))
        self.assertNotIn("test-key", str(raised.exception))

    def test_connection_failure_raises_connection_error(self):
        with patch("repo_router.jev.urlopen", side_effect=URLError("offline")):
            with self.assertRaisesRegex(JevConnectionError, "Cannot connect to OpenRouter"):
                self.strategy.decide("task", self.entries)

    def test_preserves_probabilities_confidence_model_and_usage(self):
        body = json.dumps(
            {
                "model": "typesafe/jev-1.13",
                "usage": {"input_tokens": 42, "output_tokens": 0, "cost": 0.00001},
                "answers": {
                    "route": {
                        "choice": "src",
                        "confidence": 0.6,
                        "answer_confidence": 0.8,
                        "probabilities": {"src": 0.8, "tests": 0.1, "laya_py": 0.1},
                    }
                },
            }
        ).encode()
        with self.mock_response(body):
            self.strategy.decide("task", self.entries)

        self.assertEqual(
            self.strategy.decision_metadata(),
            {
                "confidence": 0.6,
                "answer_confidence": 0.8,
                "probabilities": {"src": 0.8, "tests": 0.1, "laya_py": 0.1},
                "model": "typesafe/jev-1.13",
                "usage": {"input_tokens": 42, "output_tokens": 0, "cost": 0.00001},
            },
        )
        self.assertIsNone(self.strategy.decide("task", []))
        self.assertIsNone(self.strategy.decision_metadata())


if __name__ == "__main__":
    unittest.main()
