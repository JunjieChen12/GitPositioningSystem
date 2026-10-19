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
    assert "Git GPS bootstrap required" not in output.out
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


@pytest.mark.parametrize("executable", [None, "/custom/git-gps", ".venv/bin/git-gps"])
def test_missing_router_delegates_without_subprocess_or_writes(setup_hook, monkeypatch, capsys, executable):
    root, run, which, invoke = setup_hook
    (root / "ROUTER.md").unlink()
    if executable:
        monkeypatch.setenv("GIT_GPS_EXECUTABLE", executable)
    prompt = 'Add a weekly PnL view; $(echo untouched) "with tests"\n--trace'
    assert invoke({"prompt": prompt, "cwd": str(root / "src")}) == 0
    assert run.call_count == 1  # Git discovery only: no init, Jev, or nested Codex.
    assert not list(root.rglob("ROUTER.md"))
    output = capsys.readouterr()
    resolved = (str(root / executable) if executable and not Path(executable).is_absolute()
                else executable or "/bin/git-gps")
    assert output.out == hook.bootstrap_context(root, resolved) + "\n"
    assert "SAME turn" in output.out
    assert "Read the important source files" in output.out
    assert "ORIGINAL request" in output.out
    assert "relevant_paths and search_roots before broad" in output.out
    assert "--multi --top-k 4 --threshold 0.5 --json" in output.out
    assert "private-test-secret" not in output.out
    assert output.err == ""


def test_missing_router_without_executable_still_instructs_codex(setup_hook, capsys):
    root, run, which, invoke = setup_hook
    (root / "ROUTER.md").unlink()
    which.return_value = None
    assert invoke() == 0
    assert run.call_count == 1
    output = capsys.readouterr()
    assert "Git GPS bootstrap required." in output.out
    assert "executable unavailable" in output.err


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
    assert handler["timeout"] == 120
    assert "git rev-parse --show-toplevel" in handler["command"]
    assert '"$repo_root/.codex/hooks/gitgps_route.py"' in handler["command"]


def test_agent_created_router_then_routes_original_prompt(setup_hook, monkeypatch, capsys):
    """Simulate the agent writing routers, then run the real CLI and Jev transport."""
    import contextlib
    from repo_router.cli import main as cli_main

    root, run, which, invoke = setup_hook
    (root / "ROUTER.md").unlink()
    prompt = "Add a weekly PnL view to the monthly page."
    assert invoke({"prompt": prompt, "cwd": str(root)}) == 0
    assert "Git GPS bootstrap required." in capsys.readouterr().out
    # This write represents the current Codex turn inspecting code and creating a router.
    source = root / "src/jev.py"
    source.write_text("def route_task(task): return task\n")
    assert "def route_task" in source.read_text()
    (root / "ROUTER.md").write_text("## Important Files\n- src/jev.py — Routes coding tasks\n")
    reply = Mock()
    reply.__enter__ = Mock(return_value=reply)
    reply.__exit__ = Mock(return_value=False)
    def respond(request, **kwargs):
        questions = json.loads(request.data)["questions"]
        reply.read.return_value = json.dumps({"answers": {
            route_id: {"type": "noul", "noul": 0.9} for route_id in questions}}).encode()
        return reply
    http = Mock(side_effect=respond)
    monkeypatch.setattr("repo_router.jev.urlopen", http)
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        assert cli_main(["route", str(root), prompt, "--multi", "--top-k", "4",
                         "--threshold", "0.5", "--json"]) == 0
    result = json.loads(stdout.getvalue())
    assert result["relevant_paths"] == ["src/jev.py"]
    sent = http.call_args.args[0]
    assert json.loads(sent.data)["state"] == prompt
    assert sent.full_url == "https://openrouter.ai/api/alpha/decisions"
    assert sent.get_header("Authorization") == "Bearer private-test-secret"
    # On the next submit the existing-router path routes immediately.
    run.reset_mock()
    run.side_effect = [subprocess.CompletedProcess([], 0, str(root), ""),
                       subprocess.CompletedProcess([], 0, stdout.getvalue(), "")]
    assert invoke({"prompt": prompt, "cwd": str(root)}) == 0
    assert run.call_args.args[0][3] == prompt
    output = capsys.readouterr().out
    assert "Relevant paths:" in output
    assert "bootstrap required" not in output
