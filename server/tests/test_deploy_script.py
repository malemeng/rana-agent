import shutil
import subprocess
import sys
from pathlib import Path

from scripts.deploy import parse_env

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "deploy.py"


def make_project(tmp_path: Path) -> Path:
    root = Path(__file__).resolve().parents[2]
    shutil.copy2(root / ".env.example", tmp_path / ".env.example")
    shutil.copy2(root / "docker-compose.yml", tmp_path / "docker-compose.yml")
    return tmp_path


def run_deploy(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--project-dir", str(root), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_noninteractive_hybrid_config_without_docker(tmp_path: Path) -> None:
    make_project(tmp_path)
    result = run_deploy(
        tmp_path,
        "--provider",
        "hybrid",
        "--mimo-key",
        "mimo-test-key",
        "--minimax-key",
        "mini-test-key",
        "--admin-password",
        "admin-test-password",
        "--yes",
        "--no-start",
    )
    assert result.returncode == 0, result.stderr
    values = parse_env(tmp_path / ".env")
    assert values["AGENT_AI_PROVIDER"] == "mimo"
    assert values["AGENT_LLM_PROVIDER"] == "minimax"
    assert values["AGENT_ASR_PROVIDER"] == "mimo"
    assert values["AGENT_TTS_PROVIDER"] == "mimo"
    assert values["AGENT_MIMO_API_KEY"] == "mimo-test-key"
    assert values["AGENT_MINIMAX_API_KEY"] == "mini-test-key"
    assert len(values["AGENT_SECRET_KEY"]) > 32
    assert len(values["AGENT_EXECUTOR_TOKEN"]) > 32
    assert values["COMPOSE_PROJECT_NAME"] == "rana-agent"
    assert "mini-test-key" not in result.stdout + result.stderr
    assert "mimo-test-key" not in result.stdout + result.stderr


def test_single_minimax_does_not_require_mimo_key(tmp_path: Path) -> None:
    make_project(tmp_path)
    result = run_deploy(
        tmp_path,
        "--provider",
        "minimax",
        "--minimax-key",
        "mini-test-key",
        "--minimax-region",
        "cn",
        "--yes",
        "--no-start",
    )
    assert result.returncode == 0, result.stderr
    values = parse_env(tmp_path / ".env")
    assert values["AGENT_AI_PROVIDER"] == "minimax"
    assert "AGENT_ASR_PROVIDER" not in values
    assert values["AGENT_MINIMAX_API_KEY"] == "mini-test-key"
    assert values["AGENT_MINIMAX_REGION"] == "cn"


def test_noninteractive_reports_missing_key(tmp_path: Path) -> None:
    make_project(tmp_path)
    result = run_deploy(tmp_path, "--provider", "minimax", "--yes", "--no-start")
    assert result.returncode != 0
    assert "AGENT_MINIMAX_API_KEY" in result.stderr
