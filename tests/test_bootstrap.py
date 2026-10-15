"""Real temporary Git repositories; description and HTTP models are always mocked."""

import json
import subprocess
from unittest.mock import Mock

import pytest

from repo_router.bootstrap import EXCERPT_BYTES, bootstrap_repository, inspect_repository
from repo_router.cli import main
from repo_router.generation import GenerationError, OpenRouterDescriptionModel
from repo_router.parser import parse_router


@pytest.fixture
def repository(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "main.py").write_text('from fastapi import FastAPI\napp = FastAPI()\n@app.get("/notes")\ndef notes(): return []\n')
    (tmp_path / "static").mkdir()
    (tmp_path / "static/script.js").write_text('fetch("/notes").then(renderCalendar);')
    (tmp_path / "static/style.css").write_text('.calendar {display: grid;}')
    return tmp_path


def describe(context):
    descriptions = {}
    for entry in context["entries"]:
        content = entry["excerpt"]
        if "FastAPI" in content:
            description = "FastAPI application startup and note endpoints"
        elif "fetch(" in content:
            description = "Calendar rendering and note API requests"
        elif "display: grid" in content:
            description = "Calendar grid styling"
        else:
            description = "Browser calendar rendering, note requests, and styling"
        descriptions[entry["path"]] = description
    return {"purpose": "Calendar application and note management.", "descriptions": descriptions}


@pytest.fixture
def model():
    return Mock(describe=Mock(side_effect=describe))


def test_existing_root_skips_inspection_and_model(repository, model, monkeypatch):
    original = "## Important Files\n- main.py — Notes API\n"
    (repository / "ROUTER.md").write_text(original)
    monkeypatch.setattr("repo_router.bootstrap.inspect_repository", Mock(side_effect=AssertionError))
    assert bootstrap_repository(repository / "static", model).existing
    model.describe.assert_not_called()
    assert (repository / "ROUTER.md").read_text() == original


def test_content_driven_root_and_nested_routers_parse(repository, model):
    result = bootstrap_repository(repository / "static", model)
    assert result.created == ("ROUTER.md", "static/ROUTER.md")
    root = parse_router((repository / "ROUTER.md").read_text())
    assert root.files[0].description == "FastAPI application startup and note endpoints"
    assert root.directories[0].path == "static/"
    nested = parse_router((repository / "static/ROUTER.md").read_text())
    assert len(nested.files) == 2
    assert "note API requests" in nested.files[0].description
    # Batches contain multiple file excerpts; parents receive compact child summaries.
    assert model.describe.call_count == 2
    assert len(model.describe.call_args_list[0].args[0]["entries"]) == 2
    assert "fetch(" in str(model.describe.call_args_list[0])
    bootstrap_repository(repository, model)
    assert model.describe.call_count == 2


@pytest.mark.parametrize("name", [".env", ".env.local", "credentials.json", "secrets.yaml",
    "token.txt", "private-key.txt", "id_rsa", "certificate.pem", "key.key", ".ssh/config"])
def test_secret_files_never_inspected(repository, name):
    path = repository / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("secret material")
    assert name not in inspect_repository(repository)


def test_ignores_binary_symlinks_and_generated_paths(repository, model, tmp_path):
    (repository / ".gitignore").write_text("ignored/\ntracked.py\n")
    for name in ["ignored/leak.py", "tracked.py", "node_modules/a.js", ".venv/a.py", "__pycache__/a.py",
                 "build/a.py", "dist/a.js", "coverage/a.py", "target/a.rs", "binary.py"]:
        path = repository / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x00secret" if name == "binary.py" else b"excluded contents")
    subprocess.run(["git", "add", "-f", "tracked.py"], cwd=repository, check=True)
    (repository / "link.py").symlink_to(repository / "main.py")
    (repository / "linked").symlink_to(repository / "static", target_is_directory=True)
    files = inspect_repository(repository)
    assert set(files) == {"main.py", "static/script.js", "static/style.css"}
    bootstrap_repository(repository, model)
    assert "excluded contents" not in str(model.describe.call_args_list)


def test_embedded_secrets_excluded(repository, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-sensitive")
    (repository / "config.py").write_text('API_KEY = "super-sensitive-value"')
    (repository / "keydata.py").write_text('-----BEGIN RSA PRIVATE KEY-----')
    (repository / "other.py").write_text('value = "test-key-sensitive"')
    files = inspect_repository(repository)
    assert "config.py" not in files and "keydata.py" not in files and "other.py" not in files


def test_large_files_are_bounded_and_batches_are_bounded(repository, model):
    for i in range(40):
        (repository / f"module{i:02}.py").write_text("# safe comment\n" * 10000)
    files = inspect_repository(repository)
    assert len(files["module00.py"]) < EXCERPT_BYTES + 40
    assert "truncated" in files["module00.py"]
    bootstrap_repository(repository, model)
    assert 2 < model.describe.call_count < len(files)
    for call in model.describe.call_args_list:
        assert len(call.args[0]["entries"]) <= 24
        assert len(str(call.args[0])) < 25000


@pytest.mark.parametrize("response", [None, [], {}, {"purpose": "x", "descriptions": {}},
    {"purpose": "x", "descriptions": {"../outside.py": "malicious"}}])
def test_malformed_model_output_leaves_no_routers(repository, response):
    with pytest.raises(GenerationError):
        bootstrap_repository(repository, Mock(describe=Mock(return_value=response)))
    assert not list(repository.rglob("ROUTER.md"))


def test_late_model_failure_does_not_publish_partial_routers(repository):
    model = Mock(describe=Mock(side_effect=[describe({"entries": [
        {"path": "script.js", "excerpt": "fetch("}, {"path": "style.css", "excerpt": "display: grid"}]}),
        RuntimeError("secret-provider-error")]))
    with pytest.raises(GenerationError, match="router description model failed"):
        bootstrap_repository(repository, model)
    assert not list(repository.rglob("ROUTER.md"))


@pytest.mark.parametrize("value", ["bad\n## Injection", "bad\u2028## Injection", "x" * 301, "", 'password="sensitive-value"'])
def test_invalid_descriptions_are_rejected(repository, value):
    def bad(context):
        response = describe(context)
        response["purpose"] = value
        return response
    with pytest.raises(GenerationError):
        bootstrap_repository(repository, Mock(describe=bad))
    assert not list(repository.rglob("ROUTER.md"))


def test_output_is_deterministic(repository, model):
    first = bootstrap_repository(repository, model)
    original = {p: (repository / p).read_bytes() for p in first.created}
    for p in first.created:
        (repository / p).unlink()
    assert bootstrap_repository(repository, model).created == first.created
    assert original == {p: (repository / p).read_bytes() for p in first.created}


def test_dry_run_has_no_model_calls_or_writes(repository, model):
    result = bootstrap_repository(repository, model, dry_run=True)
    assert result.created == ("ROUTER.md", "static/ROUTER.md")
    model.describe.assert_not_called()
    assert not list(repository.rglob("ROUTER.md"))


def test_existing_nested_router_is_preserved(repository, model):
    path = repository / "static/ROUTER.md"
    original = "## Important Files\n- script.js — Custom route\n"
    path.write_text(original)
    assert bootstrap_repository(repository, model).created == ("ROUTER.md",)
    assert path.read_text() == original


@pytest.mark.parametrize("contents", ["", "# Router\n", "## Important Files\n- broken\n"])
def test_invalid_existing_root_is_not_overwritten(repository, model, contents):
    (repository / "ROUTER.md").write_text(contents)
    with pytest.raises(GenerationError, match="repair it manually"):
        bootstrap_repository(repository, model)
    model.describe.assert_not_called()
    assert (repository / "ROUTER.md").read_text() == contents


def test_cli_init_and_dry_run(repository, monkeypatch, model, capsys):
    monkeypatch.setattr("repo_router.cli.bootstrap_repository", lambda p, dry_run=False:
                        bootstrap_repository(p, model, dry_run=dry_run))
    assert main(["init", str(repository), "--dry-run"]) == 0
    assert "Would create:" in capsys.readouterr().out
    assert main(["init", str(repository)]) == 0
    assert "Created:\n- ROUTER.md\n- static/ROUTER.md" in capsys.readouterr().out
    assert main(["init", str(repository)]) == 0
    assert "skipped generation" in capsys.readouterr().out


def test_cli_model_error_is_sanitized(repository, monkeypatch, capsys):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert main(["init", str(repository)]) == 1
    assert "OPENROUTER_API_KEY is required" in capsys.readouterr().err


def test_transport_model_config_and_json_contract(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "mock-key")
    monkeypatch.setenv("GIT_GPS_ROUTER_MODEL", "provider/small-model")
    monkeypatch.setenv("GIT_GPS_ROUTER_URL", "https://example.invalid/chat/completions")
    reply = Mock()
    reply.__enter__ = Mock(return_value=reply)
    reply.__exit__ = Mock(return_value=False)
    reply.read.return_value = json.dumps({"choices": [{"message": {"content": json.dumps({
        "purpose": "Notes API", "descriptions": {"main.py": "Note endpoints"}})}}]}).encode()
    request = Mock(return_value=reply)
    monkeypatch.setattr("repo_router.generation.urlopen", request)
    model = OpenRouterDescriptionModel()
    assert model.describe({"entries": []})["purpose"] == "Notes API"
    sent = request.call_args.args[0]
    assert sent.full_url == "https://example.invalid/chat/completions"
    assert json.loads(sent.data)["model"] == "provider/small-model"
    assert sent.get_header("Authorization") == "Bearer mock-key"


def test_transport_failure_sanitized(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "mock-key")
    monkeypatch.setattr("repo_router.generation.urlopen", Mock(side_effect=RuntimeError("sensitive")))
    with pytest.raises(GenerationError, match="failed or returned invalid JSON") as error:
        OpenRouterDescriptionModel().describe({})
    assert "sensitive" not in str(error.value)
