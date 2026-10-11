"""Hosted Jev routing through OpenRouter's Decisions API."""

from collections.abc import Iterable, Mapping
import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from repo_router.decision import RouteEntry, validate_multi_selection
from repo_router.parser import DirectoryEntry
from repo_router.route_ids import entries_by_semantic_id


DEFAULT_BASE_URL = "https://openrouter.ai/api/alpha/decisions"
DEFAULT_MODEL = "typesafe/jev-1.13"
_QUESTION_ID = "route"


class JevError(Exception):
    """Base error for a hosted Jev routing decision."""


class JevConfigurationError(JevError):
    """Jev cannot be used with the current configuration."""


class JevConnectionError(JevError):
    """OpenRouter could not be reached."""


class JevResponseError(JevError):
    """OpenRouter returned an unusable Jev response."""


class JevRoutingStrategy:
    """Use Jev choice for one route or independent relevance for many routes."""

    def __init__(self, timeout: float = 60.0) -> None:
        self.base_url = os.environ.get("JEV_BASE_URL", DEFAULT_BASE_URL)
        self.model = os.environ.get("JEV_MODEL", DEFAULT_MODEL)
        self.api_key = os.environ.get("OPENROUTER_API_KEY")
        self.http_referer = os.environ.get("OPENROUTER_HTTP_REFERER")
        self.app_title = os.environ.get("OPENROUTER_APP_TITLE")
        self.timeout = timeout
        self._decision_metadata: dict[str, object] | None = None

    def decision_metadata(self) -> Mapping[str, object] | None:
        """Return probabilities, confidence, model, and usage from the last answer."""
        return self._decision_metadata

    def decide_many(
        self, task: str, entries: Iterable[RouteEntry], *,
        top_k: int | None = 3, threshold: float | None = None,
    ) -> list[RouteEntry]:
        """Score routes independently, exclude zero, threshold, then limit.

        All relevance questions are batched into one request. Missing or invalid
        answers are unscored; ties retain router order. Scores are never normalized.
        """
        validate_multi_selection(top_k, threshold)
        self._decision_metadata = None
        by_id = entries_by_semantic_id(entries)
        if not by_id:
            return []
        questions = {}
        for route_id, entry in by_id.items():
            route = (
                f"{'directory' if isinstance(entry, DirectoryEntry) else 'file'} "
                f"{entry.path}: {entry.description}"
            )
            questions[route_id] = {
                "type": "noul",
                "instructions": (
                    "Is this repository route relevant to completing the coding task? "
                    "Assess this route independently; multiple routes may be relevant."
                ),
                "criteria": {
                    "true": f"This task likely requires inspecting or modifying this {route}.",
                    "false": f"This task does not require inspecting or modifying this {route}.",
                },
            }
        result = self._request({"model": self.model, "state": task, "questions": questions})
        answers = result.get("answers")
        if not isinstance(answers, dict):
            raise JevResponseError("Jev response has no relevance answers")
        scores = {}
        errors = {}
        confidence = {}
        answer_confidence = {}
        for route_id in by_id:
            answer = answers.get(route_id)
            if not isinstance(answer, dict):
                errors[route_id] = "missing or malformed relevance answer"
                continue
            score = answer.get("noul")
            if (answer.get("type", "noul") != "noul"
                    or not isinstance(score, (int, float)) or isinstance(score, bool)
                    or not 0 <= score <= 1):
                errors[route_id] = "invalid noul relevance score"
                continue
            scores[route_id] = score
            if answer.get("confidence") is not None:
                confidence[route_id] = answer["confidence"]
            if answer.get("answer_confidence") is not None:
                answer_confidence[route_id] = answer["answer_confidence"]
        self._decision_metadata = {
            "relevance_scores": scores,
            # The traversal's probability map also contains independent scores.
            "probabilities": dict(scores),
            **{key: result[key] for key in ("model", "usage") if result.get(key) is not None},
            **({"confidence": confidence} if confidence else {}),
            **({"answer_confidence": answer_confidence} if answer_confidence else {}),
            **({"score_errors": errors} if errors else {}),
        }
        ranked = [
            (entry, scores[route_id]) for route_id, entry in by_id.items()
            if route_id in scores and scores[route_id] > 0
            and (threshold is None or scores[route_id] >= threshold)
        ]
        ranked.sort(key=lambda item: item[1], reverse=True)
        return [entry for entry, score in ranked][:top_k]

    def decide(self, task: str, entries: Iterable[RouteEntry]) -> RouteEntry | None:
        self._decision_metadata = None
        choices = list(entries)
        if not choices:
            return None
        if not self.api_key or not self.api_key.strip():
            raise JevConfigurationError("OPENROUTER_API_KEY is required for Jev routing")

        by_id = entries_by_semantic_id(choices)
        criteria = {
            choice_id: (
                f"{'directory' if isinstance(entry, DirectoryEntry) else 'file'} "
                f"{entry.path}: {entry.description}"
            )
            for choice_id, entry in by_id.items()
        }
        payload = {
            "model": self.model,
            "state": task,
            "questions": {
                _QUESTION_ID: {
                    "type": "choice",
                    "instructions": (
                        "Choose the repository route where the implementation most "
                        "relevant to this task is located."
                    ),
                    "criteria": criteria,
                }
            },
        }
        result = self._request(payload)
        answers = result.get("answers")
        answer = answers.get(_QUESTION_ID) if isinstance(answers, dict) else None
        if not isinstance(answer, dict) or "choice" not in answer:
            raise JevResponseError("Jev response has no route answer")

        self._decision_metadata = {
            key: dict(value) if isinstance(value, dict) else value
            for key, value in (
                ("confidence", answer.get("confidence")),
                ("answer_confidence", answer.get("answer_confidence")),
                ("probabilities", answer.get("probabilities")),
                ("model", result.get("model")),
                ("usage", result.get("usage")),
            )
            if value is not None
        } or None
        choice_id = answer["choice"]
        if choice_id is None:
            raise JevResponseError("Jev response has no route answer")
        if not isinstance(choice_id, str) or choice_id not in by_id:
            raise JevResponseError(f"Jev returned an unknown route ID: {choice_id!r}")
        return by_id[choice_id]

    def _request(self, payload: dict[str, object]) -> dict[str, object]:
        """Send a Decisions request using the shared transport validation."""
        if not self.api_key or not self.api_key.strip():
            raise JevConfigurationError("OPENROUTER_API_KEY is required for Jev routing")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if self.http_referer:
            headers["HTTP-Referer"] = self.http_referer
        if self.app_title:
            headers["X-OpenRouter-Title"] = self.app_title
        request = Request(
            self.base_url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )

        try:
            with urlopen(request, timeout=self.timeout) as response:
                body = response.read()
        except HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            error_body = error_body.replace(self.api_key, "[REDACTED]")
            raise JevResponseError(f"OpenRouter returned HTTP {exc.code}: {error_body}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise JevConnectionError(f"Cannot connect to OpenRouter at {self.base_url}: {exc}") from exc

        if not body:
            raise JevResponseError("OpenRouter returned an empty Jev response")
        try:
            result = json.loads(body)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise JevResponseError("OpenRouter returned malformed Jev JSON") from exc
        if not isinstance(result, dict):
            raise JevResponseError("Jev response must be a JSON object")
        return result
