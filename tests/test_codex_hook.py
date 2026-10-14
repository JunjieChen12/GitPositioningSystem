"""Hook tests mock every subprocess; no hosted service is contacted."""

import importlib.util
import io
import json
from pathlib import Path
import subprocess
from unittest.mock import Mock

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / ".codex/hooks/gitgps_route.py"
spec = importlib.util.spec_from_file_location("gitgps_hook", SCRIPT)
hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hook)


@pytest.fixture
def setup_hook(tmp_path, monkeypatch):
    (tmp_path / "ROUTER.md").touch()
    (tmp_path / "src").mkdir()
    (tmp_path / "src/jev.py").touch()
    (tmp_path / "tests").mkdir()
    monkeypatch.delenv("GIT_GPS_EXECUTABLE", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "private-test-secret")
    which = Mock(return_value="/bin/git-gps")
    monkeypatch.setattr(hook.shutil, "which", which)
    payload = {"status": "found", "relevant_paths": ["src/jev.py"], "search_roots": ["tests"]}
    run = Mock(side_effect=[
        subprocess.CompletedProcess([], 0, str(tmp_path) + "\n", ""),
        subprocess.CompletedProcess([], 0, json.dumps(payload), "private-test-secret"),
    ])
    monkeypatch.setattr(hook.subprocess, "run", run)

    def invoke(event=None, raw=None):
        monkeypatch.setattr(hook.sys, "stdin", io.StringIO(
            raw if raw is not None else json.dumps(event if event is not None else {
                "prompt": "Update Jev", "cwd": str(tmp_path),
            })
        ))
        return hook.main()
    return tmp_path, run, which, invoke


@pytest.mark.parametrize("prompt", [None, "", "  \n", 12])
def test_empty_or_missing_prompt(setup_hook, capsys, prompt):
    root, run, which, invoke = setup_hook
    assert invoke({"cwd": str(root), **({"prompt": prompt} if prompt is not None else {})}) == 0
    run.assert_not_called()
    assert capsys.readouterr().out == ""


def test_success_and_nested_cwd(setup_hook, capsys):
    root, run, which, invoke = setup_hook
    prompt = 'Update Jev; $(echo secret) "and tests"\n--trace'
    assert invoke({"prompt": prompt, "cwd": str(root / "src")}) == 0
    first, second = run.call_args_list
    assert first.args[0] == ["git", "rev-parse", "--show-toplevel"]
    assert first.kwargs == dict(cwd=str(root / "src"), capture_output=True, text=True, timeout=3)
    assert second.args[0] == ["/bin/git-gps", "route", ".", prompt, "--strategy", "jev",
                              "--multi", "--top-k", "4", "--threshold", "0.5", "--json"]
    assert second.kwargs == dict(cwd=root, capture_output=True, text=True, timeout=25)
    which.assert_called_once_with("git-gps")
    output = capsys.readouterr()
    assert "Relevant paths:\n- src/jev.py" in output.out
    assert "Search roots:\n- tests" in output.out
    assert str(root) in output.out
    assert "Expand beyond them if the route is insufficient." in output.out
    assert "private-test-secret" not in output.out + output.err


@pytest.mark.parametrize("override", ["/custom/git-gps", ".venv/bin/git-gps"])
def test_executable_override(setup_hook, monkeypatch, override):
    root, run, which, invoke = setup_hook
    monkeypatch.setenv("GIT_GPS_EXECUTABLE", override)
    assert invoke() == 0
    expected = override if override.startswith("/") else str(root / override)
    assert run.call_args.args[0][0] == expected
    which.assert_not_called()


def test_missing_executable(setup_hook, capsys):
    root, run, which, invoke = setup_hook
    which.return_value = None
    assert invoke() == 0
    assert run.call_count == 1
    assert capsys.readouterr().out == ""


def test_missing_router(setup_hook, capsys):
    root, run, which, invoke = setup_hook
    (root / "ROUTER.md").unlink()
    assert invoke() == 0
    assert run.call_count == 1
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("result", [
    subprocess.CompletedProcess([], 1, "", "private-test-secret"),
    subprocess.TimeoutExpired(["git-gps", "private-test-secret"], 25),
    FileNotFoundError("private-test-secret"),
    RuntimeError("private-test-secret"),
    subprocess.CompletedProcess([], 0, "not JSON private-test-secret", ""),
])
def test_routing_failures_do_not_block_or_expose_secrets(setup_hook, capsys, result):
    root, run, which, invoke = setup_hook
    run.side_effect = [subprocess.CompletedProcess([], 0, str(root), ""), result]
    assert invoke() == 0
    output = capsys.readouterr()
    assert output.out == ""
    assert "private-test-secret" not in output.err
    if isinstance(result, subprocess.TimeoutExpired):
        assert "timed out" in output.err


@pytest.mark.parametrize("result", [
    subprocess.CompletedProcess([], 128, "", "not a repository"),
    subprocess.CompletedProcess([], 0, "", ""),
    subprocess.TimeoutExpired(["git"], 3),
    FileNotFoundError("git"),
])
def test_repo_discovery_failure(setup_hook, capsys, result):
    root, run, which, invoke = setup_hook
    run.side_effect = [result]
    assert invoke() == 0
    assert run.call_count == 1
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("raw", ['{', '[]', 'null', '{"prompt":"task"}', '{"prompt":"task","cwd":12}'])
def test_invalid_event(setup_hook, capsys, raw):
    root, run, which, invoke = setup_hook
    assert invoke(raw=raw) == 0
    run.assert_not_called()
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("payload", [
    [], {}, {"status": []},
    {"status": "unknown", "relevant_paths": [], "search_roots": []},
    {"status": "found", "relevant_paths": "src/jev.py", "search_roots": []},
    {"status": "found", "relevant_paths": ["../escape"], "search_roots": []},
    {"status": "found", "relevant_paths": ["/etc/passwd"], "search_roots": []},
    {"status": "found", "relevant_paths": ["file\ninstruction"], "search_roots": []},
    {"status": "found", "relevant_paths": [42], "search_roots": []},
])
def test_malformed_response(setup_hook, capsys, payload):
    root, run, which, invoke = setup_hook
    run.side_effect = [subprocess.CompletedProcess([], 0, str(root), ""),
                       subprocess.CompletedProcess([], 0, json.dumps(payload), "")]
    assert invoke() == 0
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("paths,roots,title", [
    (["src/jev.py"], [], "Relevant paths:"),
    ([], ["tests"], "Search roots:"),
    ([], [], ""),
])
def test_independent_result_sections(setup_hook, paths, roots, title):
    root, *_ = setup_hook
    context = hook.routing_context(json.dumps({"status": "missing_router",
        "relevant_paths": paths, "search_roots": roots}), root)
    assert title in context
    if not title:
        assert context == ""


def test_secret_in_returned_path_is_never_printed(setup_hook, capsys):
    root, run, which, invoke = setup_hook
    (root / "private-test-secret").touch()
    run.side_effect = [subprocess.CompletedProcess([], 0, str(root), ""),
        subprocess.CompletedProcess([], 0, json.dumps({"status": "found",
            "relevant_paths": ["private-test-secret"], "search_roots": []}), "")]
    assert invoke() == 0
    output = capsys.readouterr()
    assert output.out == ""
    assert "private-test-secret" not in output.err


def test_bounds_and_deduplication(setup_hook):
    root, *_ = setup_hook
    paths = []
    for i in range(30):
        name = f"file{i}.py"
        (root / name).touch()
        paths.extend([name, name])
    context = hook.routing_context(json.dumps({"status": "found",
        "relevant_paths": paths, "search_roots": []}), root)
    assert context.count("\n- ") == 16


def test_hook_configuration():
    config = json.loads((SCRIPT.parents[1] / "hooks.json").read_text())
    handler = config["hooks"]["UserPromptSubmit"][0]["hooks"][0]
    assert handler["type"] == "command"
    assert handler["timeout"] == 30
    assert "git rev-parse --show-toplevel" in handler["command"]
    assert '"$repo_root/.codex/hooks/gitgps_route.py"' in handler["command"]
