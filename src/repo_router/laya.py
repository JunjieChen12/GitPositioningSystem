"""Local Laya choice strategy for bounded repository route entries."""

from collections.abc import Iterable, Mapping
import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from repo_router.decision import RouteEntry
from repo_router.parser import DirectoryEntry
from repo_router.route_ids import entries_by_semantic_id


DEFAULT_BASE_URL = "http://localhost:8000"
DEFAULT_ENDPOINT_PATH = "/v1/systemone"
_QUESTION_ID = "route"


class LayaError(Exception):
    """Base error for a local Laya routing decision."""


class LayaConnectionError(LayaError):
    """The local Laya server could not be reached."""


class LayaResponseError(LayaError):
    """The local Laya server returned an unusable response."""


class LayaRoutingStrategy:
    """Ask a local Laya choice endpoint to select an existing route ID."""

    def __init__(
        self,
        base_url: str | None = None,
        endpoint_path: str | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.base_url = base_url or os.environ.get("LAYA_BASE_URL", DEFAULT_BASE_URL)
        self.endpoint_path = endpoint_path or os.environ.get(
            "LAYA_ENDPOINT_PATH", DEFAULT_ENDPOINT_PATH
        )
        self.timeout = timeout
        self._decision_metadata: dict[str, object] | None = None

    def decision_metadata(self) -> Mapping[str, object] | None:
        """Return confidence and probabilities from the last Laya answer."""
        return self._decision_metadata

    def decide(self, task: str, entries: Iterable[RouteEntry]) -> RouteEntry | None:
        self._decision_metadata = None
        choices = list(entries)
        if not choices:
            return None

        by_id = entries_by_semantic_id(choices)
        available = [
            {
                "id": choice_id,
                "path": entry.path,
                "description": entry.description,
                "type": "directory" if isinstance(entry, DirectoryEntry) else "file",
            }
            for choice_id, entry in by_id.items()
        ]
        payload = {
            "state": {
                "body": task
            },
            "questions": {
                _QUESTION_ID: {
                    "type": "choice",
                    "instructions": (
                        "Choose the repository route where the implementation for the task "
                        "most likely lives. Prefer source-code directories for implementation "
                        "questions. Choose test directories only when the task is specifically "
                        "about tests. Choose documentation only when the task is specifically "
                        "about documentation."
                    ),
                    "criteria": {
                        item["id"]: (
                            f"{item['type']} {item['path']}: {item['description']}"
                        )
                        for item in available
                    },
                }
            },
        }
        endpoint = f"{self.base_url.rstrip('/')}/{self.endpoint_path.lstrip('/')}"
        request = Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urlopen(request, timeout=self.timeout) as response:
                body = response.read()
        except HTTPError as exc:
            raise LayaResponseError(f"Laya returned HTTP {exc.code}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise LayaConnectionError(f"Cannot connect to Laya at {endpoint}: {exc}") from exc

        if not body:
            raise LayaResponseError("Laya returned an empty response")
        try:
            result = json.loads(body)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise LayaResponseError("Laya returned malformed JSON") from exc

        if not isinstance(result, dict):
            raise LayaResponseError("Laya response must be a JSON object")
        answers = result.get("answers")
        answer = answers.get(_QUESTION_ID) if isinstance(answers, dict) else None
        if not isinstance(answer, dict) or "choice" not in answer:
            raise LayaResponseError("Laya response has no route choice")

        choice_id = answer["choice"]
        self._decision_metadata = {
            key: dict(value) if key == "probabilities" and isinstance(value, dict) else value
            for key in ("confidence", "answer_confidence", "probabilities")
            if (value := answer.get(key)) is not None
        } or None
        if choice_id is None:
            return None
        if not isinstance(choice_id, str) or choice_id not in by_id:
            raise LayaResponseError(f"Laya returned an unknown route ID: {choice_id!r}")
        return by_id[choice_id]
