"""Deterministic bootstrap inspection never calls a model or writes routers."""

import json
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

from repo_router.bootstrap import BootstrapError, EXCERPT_BYTES, bootstrap_repository, inspect_repository
from repo_router.cli import main

@pytest.fixture
def repository(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "main.py").write_text('from fastapi import FastAPI\napp = FastAPI()\n@app.get("/notes")\ndef notes(): return []\n')
    (tmp_path / "static").mkdir()
    (tmp_path / "static/script.js").write_text('fetch("/notes").then(renderCalendar);')
    (tmp_path / "static/style.css").write_text('.calendar {display: grid;}')
    return tmp_path



def test_existing_root_skips_inspection(repository, monkeypatch):
    original = "## Important Files\n- main.py — Notes API\n"
    (repository / "ROUTER.md").write_text(original)
    monkeypatch.setattr("repo_router.bootstrap.inspect_repository", Mock(side_effect=AssertionError))
    result = bootstrap_repository(repository / "static")
    assert result["status"] == "router_exists"
    assert result["repository_root"] == str(repository)
    assert (repository / "ROUTER.md").read_text() == original


def test_missing_router_request_is_deterministic_without_network_or_writes(repository, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    request = bootstrap_repository(repository / "static")
    assert request == {"status": "router_required", "repository_root": str(repository),
                       "suggested_directories": ["static"],
                       "files_to_inspect": ["main.py", "static/script.js", "static/style.css"]}
    assert bootstrap_repository(repository) == request
    assert not list(repository.rglob("ROUTER.md"))

@pytest.mark.parametrize("name", [".env", ".env.local", "credentials.json", "secrets.yaml",
    "token.txt", "private-key.txt", "id_rsa", "certificate.pem", "key.key", ".ssh/config"])
def test_secret_files_never_inspected(repository, name):
    path = repository / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("secret material")
    assert name not in inspect_repository(repository)


def test_ignores_binary_symlinks_and_generated_paths(repository):
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



def test_embedded_secrets_excluded(repository):
    (repository / "config.py").write_text('API_KEY = "super-sensitive-value"')
    (repository / "keydata.py").write_text('-----BEGIN RSA PRIVATE KEY-----')
    files = inspect_repository(repository)
    assert "config.py" not in files and "keydata.py" not in files


def test_excerpts_are_bounded_and_not_in_request(repository):
    (repository / "large.py").write_text("# safe comment\n" * 10000)
    files = inspect_repository(repository)
    assert len(files["large.py"]) < EXCERPT_BYTES + 40
    assert "truncated" in files["large.py"]
    assert "# safe comment" not in json.dumps(bootstrap_repository(repository))


def test_empty_repository_requests_agent_bootstrap(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    assert bootstrap_repository(tmp_path)["status"] == "router_required"


@pytest.mark.parametrize("contents", ["", "# Router\n", "## Important Files\n- broken\n"])
def test_invalid_existing_root_is_not_overwritten(repository, contents):
    (repository / "ROUTER.md").write_text(contents)
    with pytest.raises(BootstrapError, match="repair it manually"):
        bootstrap_repository(repository)
    assert (repository / "ROUTER.md").read_text() == contents


@pytest.mark.parametrize("flags", [[], ["--dry-run"], ["--json"], ["--dry-run", "--json"]])
def test_cli_init_is_inspection_only(repository, monkeypatch, capsys, flags):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert main(["init", str(repository), *flags]) == 0
    output = capsys.readouterr()
    assert output.err == ""
    if "--json" in flags:
        assert json.loads(output.out) == bootstrap_repository(repository)
    else:
        assert "coding agent must inspect source files and create ROUTER.md" in output.out
    assert not list(repository.rglob("ROUTER.md"))


def test_init_failure_is_sanitized(tmp_path, capsys):
    assert main(["init", str(tmp_path), "--json"]) == 1
    assert "cannot find Git repository root" in capsys.readouterr().err


def test_only_jev_runtime_reads_openrouter_key():
    source = Path(__file__).resolve().parents[1] / "src/repo_router"
    users = [p.name for p in source.glob("*.py") if "OPENROUTER_API_KEY" in p.read_text()]
    assert users == ["jev.py"]
    assert not (source / "generation.py").exists()
    for path in source.glob("*.py"):
        text = path.read_text().lower()
        assert "gemini" not in text
        assert "chat/completions" not in text
        assert "git_gps_router_model" not in text
        assert "git_gps_router_url" not in text


def test_init_with_key_still_makes_no_model_calls(repository, monkeypatch, capsys):
    monkeypatch.setenv("OPENROUTER_API_KEY", "unused-bootstrap-key")
    assert main(["init", str(repository), "--json"]) == 0
    output = capsys.readouterr()
    assert json.loads(output.out)["status"] == "router_required"
    assert "unused-bootstrap-key" not in output.out + output.err
    assert not list(repository.rglob("ROUTER.md"))


def test_existing_router_cli_json(repository, capsys):
    (repository / "ROUTER.md").write_text("## Important Files\n- main.py — Notes API\n")
    assert main(["init", str(repository), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "router_exists"
