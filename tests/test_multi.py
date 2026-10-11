"""Multi-route tests: all hosted responses are mocked."""

import json
from unittest.mock import MagicMock

import pytest

from repo_router.cli import main
from repo_router.engine import RouteStatus, route_repository_multi
from repo_router.jev import JevConfigurationError, JevResponseError, JevRoutingStrategy
from repo_router.parser import DirectoryEntry, FileEntry
from repo_router.router import RouterError


@pytest.fixture
def hosted(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    response = MagicMock()
    response.__enter__.return_value = response
    http = MagicMock(return_value=response)
    monkeypatch.setattr("repo_router.jev.urlopen", http)

    def answers(*values):
        response.read.side_effect = [json.dumps({"model": "mock/jev", "usage": {"input_tokens": 42}, "answers": value}).encode()
                                     for value in values]
        return http
    return answers


ENTRIES = [FileEntry(name, name) for name in ("models.py", "schemas.py", "main.py")]
SCORES = {"main_py": 0.72, "models_py": 0.86, "schemas_py": 0.79}


def relevance(scores):
    return {key: {"type": "noul", "noul": value} for key, value in scores.items()}


@pytest.mark.parametrize("options, expected", [
    ({"top_k": 2}, ["models.py", "schemas.py"]),
    ({"top_k": None, "threshold": 0.79}, ["models.py", "schemas.py"]),
    ({"top_k": 1, "threshold": 0.75}, ["models.py"]),
    ({"threshold": 0.9}, []),
    ({}, ["models.py", "schemas.py", "main.py"]),
])
def test_independent_selection(hosted, options, expected):
    http = hosted(relevance(SCORES))
    strategy = JevRoutingStrategy()
    result = strategy.decide_many("task", iter(ENTRIES), **options)
    assert [entry.path for entry in result] == expected
    questions = json.loads(http.call_args.args[0].data)["questions"]
    assert list(questions) == ["models_py", "schemas_py", "main_py"]
    assert all(question["type"] == "noul" for question in questions.values())
    assert strategy.decision_metadata()["relevance_scores"] == SCORES
    assert http.call_count == 1


@pytest.mark.parametrize("score", [None, "0.9", True, False, -0.1, 1.1,
                                    float("nan"), float("inf"), [], {}])
def test_malformed_score_skips_only_that_route(hosted, score):
    hosted(relevance({"models_py": score, "schemas_py": 0.9, "main_py": 0.8}))
    strategy = JevRoutingStrategy()
    assert strategy.decide_many("task", ENTRIES) == ENTRIES[1:]
    assert "models_py" not in strategy.decision_metadata()["relevance_scores"]
    assert "models_py" in strategy.decision_metadata()["score_errors"]


@pytest.mark.parametrize("answer", [None, [], {}, {"choice": "models_py"},
                                      {"type": "choice", "noul": 0.9}])
def test_missing_or_malformed_answer_skipped(hosted, answer):
    answers = relevance({"schemas_py": 0.9})
    answers["models_py"] = answer
    hosted(answers)
    strategy = JevRoutingStrategy()
    assert strategy.decide_many("task", ENTRIES) == [ENTRIES[1]]
    assert set(strategy.decision_metadata()["score_errors"]) == {"models_py", "main_py"}


def test_all_missing_answers_return_no_selection(hosted):
    hosted({})
    strategy = JevRoutingStrategy()
    assert strategy.decide_many("task", ENTRIES) == []
    assert strategy.decision_metadata()["relevance_scores"] == {}


@pytest.mark.parametrize("options", [{}, {"threshold": 0}, {"top_k": None}])
def test_zero_scores_never_fill_top_k(hosted, options):
    hosted(relevance({"models_py": 0.8, "schemas_py": 0, "main_py": 0}))
    assert JevRoutingStrategy().decide_many("task", ENTRIES, **options) == ENTRIES[:1]


def test_unknown_ids_ignored_and_ties_stable(hosted):
    hosted(relevance({"invented_py": 1, "schemas_py": 0.8, "models_py": 0.8, "main_py": -1}))
    assert JevRoutingStrategy().decide_many("task", ENTRIES) == ENTRIES[:2]


@pytest.mark.parametrize("body", [b"", b"not json", b"[]", b'{"answers": []}', b'{}'])
def test_malformed_response_rejected(hosted, body):
    http = hosted()
    http.return_value.read.side_effect = None
    http.return_value.read.return_value = body
    with pytest.raises(JevResponseError):
        JevRoutingStrategy().decide_many("task", ENTRIES)


def test_task_relevant_to_source_and_tests(hosted):
    entries = [DirectoryEntry("src/", "source code and CLI"),
               DirectoryEntry("tests/", "automated tests for routing and CLI"),
               DirectoryEntry("docs/", "documentation")]
    http = hosted(relevance({"src": 0.95, "tests": 0.90, "docs": 0.05}))
    task = "Update Jev routing and its CLI behavior and tests"
    assert JevRoutingStrategy().decide_many(task, entries, threshold=0.50) == entries[:2]
    payload = json.loads(http.call_args.args[0].data)
    assert payload["state"] == task
    assert payload["model"] == "typesafe/jev-1.13"
    for route_id, entry in zip(("src", "tests", "docs"), entries):
        question = payload["questions"][route_id]
        assert question["type"] == "noul"
        for criterion in question["criteria"].values():
            assert f"directory {entry.path}: {entry.description}" in criterion


def test_high_scores_do_not_compete(hosted):
    entries = [DirectoryEntry("src/", "source code"), DirectoryEntry("tests/", "tests")]
    hosted(relevance({"src": 0.91, "tests": 0.88}))
    strategy = JevRoutingStrategy()
    assert strategy.decide_many("task", entries, threshold=0.8) == entries
    assert strategy.decision_metadata()["relevance_scores"] == {"src": 0.91, "tests": 0.88}


def test_semantic_question_ids_and_reserved_collisions(hosted):
    names = ["README.md", "jev.py", "cli.py", "foo-bar.py", "foo_bar.py", "foo_bar_py_2"]
    entries = [FileEntry(name, f"description of {name}") for name in names]
    ids = ["readme_md", "jev_py", "cli_py", "foo_bar_py", "foo_bar_py_3", "foo_bar_py_2"]
    http = hosted(relevance(dict.fromkeys(ids, 0.9)))
    assert JevRoutingStrategy().decide_many("task", entries, top_k=None) == entries
    questions = json.loads(http.call_args.args[0].data)["questions"]
    assert list(questions) == ids
    for route_id, entry in zip(ids, entries):
        assert f"file {entry.path}: {entry.description}" in questions[route_id]["criteria"]["true"]


def test_empty_entries_without_network(hosted):
    http = hosted()
    assert JevRoutingStrategy().decide_many("task", []) == []
    http.assert_not_called()


def router(directory, files=(), directories=()):
    directory.mkdir(parents=True, exist_ok=True)
    text = "## Directories\n" + "".join(f"- {name} — directory\n" for name in directories)
    text += "## Important Files\n" + "".join(f"- {name} — file\n" for name in files)
    (directory / "ROUTER.md").write_text(text)
    for name in files:
        (directory / name).touch()


@pytest.fixture
def repository(tmp_path):
    router(tmp_path, ["models.py", "schemas.py"], ["static/"])
    router(tmp_path / "static", ["script.js"])
    return tmp_path


def root_answer():
    answers = relevance({"models_py": 0.85, "schemas_py": 0.80, "static": 0.75})
    answers["models_py"].update(confidence=0.9, answer_confidence=0.95)
    return answers


def test_integration_mixed_branches_metadata_and_order(hosted, repository):
    http = hosted(root_answer(), relevance({"script_js": 0.95}))
    result = route_repository_multi(repository, "add field", JevRoutingStrategy())
    assert result.paths == ["models.py", "schemas.py", "static/script.js"]
    assert result.status == RouteStatus.FOUND
    assert result.search_roots == []
    assert [(s.current_directory, s.selected_path) for s in result.trace] == [
        (".", "models.py"), (".", "schemas.py"), (".", "static/"), ("static", "script.js")]
    assert result.trace[0].metadata["probability"] == 0.85
    assert result.trace[0].metadata["confidence"] == {"models_py": 0.9}
    assert result.trace[0].metadata["answer_confidence"] == {"models_py": 0.95}
    assert result.trace[0].metadata["probabilities"] == {"models_py": 0.85, "schemas_py": 0.80, "static": 0.75}
    assert result.trace[0].metadata["model"] == "mock/jev"
    assert result.trace[0].metadata["usage"] == {"input_tokens": 42}
    assert http.call_count == 2


class AllEntries:
    def decide_many(self, task, entries, **kwargs):
        return list(entries)


def test_multiple_directories_and_duplicate_paths(tmp_path):
    router(tmp_path, ["shared.py"], ["src/", "tests/"])
    router(tmp_path / "src", ["../shared.py", "src.py"])
    router(tmp_path / "tests", ["../shared.py", "test.py"])
    result = route_repository_multi(tmp_path, "task", AllEntries())
    assert result.paths == ["shared.py", "src/src.py", "tests/test.py"]
    assert result.search_roots == []
    assert result == route_repository_multi(tmp_path, "task", AllEntries())


def test_cycle_tracking_is_per_branch(tmp_path):
    router(tmp_path, directories=["src/", "tests/"])
    router(tmp_path / "src", directories=["../shared/"])
    router(tmp_path / "tests", directories=["../shared/"])
    router(tmp_path / "shared", ["shared.py"])
    result = route_repository_multi(tmp_path, "task", AllEntries())
    assert result.paths == ["shared/shared.py"]
    assert [branch.status for branch in result.branches] == [RouteStatus.FOUND, RouteStatus.FOUND]
    assert sum(step.current_directory == "shared" for step in result.trace) == 2


def test_strategy_cannot_invent_entries(repository):
    class Invented:
        def decide_many(self, task, entries, **kwargs):
            return [FileEntry("invented.py", "invented")]

    result = route_repository_multi(repository, "task", Invented())
    assert result.status == RouteStatus.NO_MATCH
    assert result.paths == []


@pytest.mark.parametrize("case,status,search", [
    ("missing_router", RouteStatus.MISSING_ROUTER, "static"),
    ("missing_directory", RouteStatus.MISSING_TARGET, "."),
    ("missing_file", RouteStatus.MISSING_TARGET, "static"),
    ("cycle", RouteStatus.CYCLE, "static"),
    ("depth", RouteStatus.MAX_DEPTH, "static"),
    ("empty", RouteStatus.NO_MATCH, "static"),
])
def test_branch_stops_preserve_partial_results(repository, case, status, search):
    options = {}
    if case == "missing_router":
        (repository / "static" / "ROUTER.md").unlink()
    elif case == "missing_directory":
        (repository / "static" / "ROUTER.md").unlink()
        (repository / "static" / "script.js").unlink()
        (repository / "static").rmdir()
    elif case == "missing_file":
        (repository / "static" / "script.js").unlink()
    elif case == "cycle":
        router(repository / "static", directories=["../"])
    elif case == "depth":
        options["max_depth"] = 0
    else:
        router(repository / "static")
    result = route_repository_multi(repository, "task", AllEntries(), **options)
    assert result.status == RouteStatus.FOUND
    assert result.paths == ["models.py", "schemas.py"]
    assert result.search_roots == [search]
    assert status in [branch.status for branch in result.branches]


@pytest.mark.parametrize("budget", [1, 2, 3, 4])
def test_global_budget(hosted, repository, budget):
    http = hosted(root_answer(), relevance({"script_js": 0.95}))
    result = route_repository_multi(repository, "task", JevRoutingStrategy(), max_nodes=budget)
    assert result.status == RouteStatus.BUDGET_EXCEEDED
    assert result.metadata["nodes_visited"] <= budget
    assert result.search_roots
    assert any(step.metadata.get("status") == "budget_exceeded"
               for step in result.trace if step.metadata)
    assert http.call_count <= budget


def test_exact_budget_completes(repository):
    assert route_repository_multi(repository, "task", AllEntries(), max_nodes=5).status == RouteStatus.FOUND


def test_empty_router(tmp_path):
    router(tmp_path)
    result = route_repository_multi(tmp_path, "task", AllEntries())
    assert result.status == RouteStatus.NO_MATCH
    assert result.search_roots == ["."]


def test_duplicate_search_roots(tmp_path):
    router(tmp_path, directories=["static/", "static/./"])
    (tmp_path / "static").mkdir()
    result = route_repository_multi(tmp_path, "task", AllEntries())
    assert result.search_roots == ["static"]


def test_escaping_path_rejected(tmp_path):
    router(tmp_path, directories=["../"])
    with pytest.raises(RouterError, match="leaves repository"):
        route_repository_multi(tmp_path, "task", AllEntries())


@pytest.mark.parametrize("options", [{"top_k": 0}, {"top_k": -1}, {"threshold": -0.1},
    {"threshold": 1.1}, {"threshold": float("nan")}, {"max_nodes": 0}, {"max_depth": -1}])
def test_api_validation(repository, options):
    with pytest.raises(ValueError):
        route_repository_multi(repository, "task", AllEntries(), **options)


@pytest.mark.parametrize("args,message", [
    (["--top-k", "0"], "positive integer"), (["--top-k", "-2"], "positive integer"),
    (["--threshold", "-0.1"], "between 0 and 1"),
    (["--threshold", "1.1"], "between 0 and 1"),
    (["--threshold", "nan"], "between 0 and 1"),
    (["--strategy", "keyword", "--multi"], "requires --strategy jev"),
    (["--strategy", "laya", "--multi"], "requires --strategy jev"),
    (["--max-nodes", "0"], "positive integer"),
])
def test_cli_invalid_input(repository, capsys, args, message):
    with pytest.raises(SystemExit) as exc:
        main(["route", str(repository), "task", *args])
    assert exc.value.code == 2
    assert message in capsys.readouterr().err


@pytest.mark.parametrize("options,expected", [
    ([], ["models.py", "schemas.py", "static/script.js"]),
    (["--top-k", "1"], ["models.py"]),
    (["--threshold", "0.83"], ["models.py"]),
    (["--top-k", "2", "--threshold", "0.81"], ["models.py"]),
])
def test_cli_multi_trace(hosted, repository, capsys, options, expected):
    hosted(root_answer(), relevance({"script_js": 0.95}))
    assert main(["route", str(repository), "task", "--strategy", "jev", "--multi",
                 "--trace", *options]) == 0
    output = capsys.readouterr().out
    assert "route trace:\n. -> models.py (0.85)" in output
    assert "status: found" in output
    assert output.split("relevant paths:\n")[1].splitlines() == [f"- {path}" for path in expected]


def test_cli_budget_reports_fallbacks(hosted, repository, capsys):
    hosted(root_answer())
    assert main(["route", str(repository), "task", "--strategy", "jev", "--multi",
                 "--max-nodes", "2", "--trace"]) == 0
    output = capsys.readouterr().out
    assert ". -> (budget_exceeded)" in output
    assert "status: budget_exceeded" in output
    assert "relevant paths:\n- models.py\nsearch roots:\n- ." in output


def test_jev_recursive_multiple_high_relevance_directories(hosted, tmp_path):
    router(tmp_path, directories=["src/", "tests/", "docs/"])
    router(tmp_path / "src", ["jev.py", "cli.py"])
    router(tmp_path / "tests", ["test_jev.py", "test_cli.py"])
    router(tmp_path / "docs", ["guide.md"])
    http = hosted(
        relevance({"src": 0.95, "tests": 0.91, "docs": 0.04}),
        relevance({"jev_py": 0.98, "cli_py": 0.86}),
        relevance({"test_jev_py": 0.97, "test_cli_py": 0.89}),
    )
    result = route_repository_multi(
        tmp_path, "Update Jev routing and its CLI behavior and tests",
        JevRoutingStrategy(), threshold=0.5,
    )
    assert result.paths == ["src/jev.py", "src/cli.py", "tests/test_jev.py", "tests/test_cli.py"]
    assert result.search_roots == []
    assert [(s.current_directory, s.selected_path, s.metadata["probability"])
            for s in result.trace] == [
        (".", "src/", 0.95), (".", "tests/", 0.91),
        ("src", "jev.py", 0.98), ("src", "cli.py", 0.86),
        ("tests", "test_jev.py", 0.97), ("tests", "test_cli.py", 0.89),
    ]
    assert http.call_count == 3
    assert [len(json.loads(call.args[0].data)["questions"])
            for call in http.call_args_list] == [3, 2, 2]


def test_cli_default_excludes_zero_routes(hosted, repository, capsys):
    hosted(relevance({"models_py": 0.91, "schemas_py": 0, "static": 0}))
    assert main(["route", str(repository), "task", "--strategy", "jev", "--multi",
                 "--trace"]) == 0
    output = capsys.readouterr().out
    assert "relevant paths:\n- models.py\n" in output
    assert ". -> models.py (0.91)" in output
    assert "schemas.py" not in output
    assert "static" not in output


def test_multi_metadata_reset_and_missing_key(hosted, monkeypatch):
    http = hosted(relevance({"models_py": 0.91}))
    strategy = JevRoutingStrategy()
    strategy.decide_many("task", ENTRIES)
    assert strategy.decision_metadata()
    assert strategy.decide_many("task", []) == []
    assert strategy.decision_metadata() is None
    monkeypatch.delenv("OPENROUTER_API_KEY")
    with pytest.raises(JevConfigurationError):
        JevRoutingStrategy().decide_many("task", ENTRIES)
    assert http.call_count == 1
