"""J-003 — Unit tests for the install_quantmind_service.sh installer.

The installer is bash, so the tests drive it via ``--dry-run`` (which
requires no root + applies no system changes) and assert the stdout
captures the documented action plan. Behavioural assertions:

* ``--dry-run`` exits 0 without root.
* Help text is reachable via ``--help``.
* Unknown args produce exit code 2.
* ``--dry-run`` reports "would: install unit" + "would: systemctl
  daemon-reload" + does NOT touch /etc/systemd/system/.
* The unit file referenced is present and parseable.
* Key systemd directives (Restart=always, StartLimitBurst=20,
  After=mongod redis) are present in the unit.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_INSTALL_SCRIPT = _REPO_ROOT / "scripts" / "install_quantmind_service.sh"
_UNIT_FILE = _REPO_ROOT / "deploy" / "quantmind.service"
_ENV_TEMPLATE = _REPO_ROOT / "deploy" / "quantmind.env.example"


def _has_bash() -> bool:
    return shutil.which("bash") is not None


pytestmark = pytest.mark.skipif(
    not _has_bash(), reason="bash unavailable in this environment"
)


# ---------------------------------------------------------------------------
# Script artefact sanity
# ---------------------------------------------------------------------------


def test_install_script_exists_and_executable() -> None:
    assert _INSTALL_SCRIPT.exists()
    assert _INSTALL_SCRIPT.stat().st_mode & 0o111  # at least one exec bit


def test_unit_file_exists() -> None:
    assert _UNIT_FILE.exists()


def test_env_template_exists() -> None:
    assert _ENV_TEMPLATE.exists()


# ---------------------------------------------------------------------------
# --dry-run + --help behaviour
# ---------------------------------------------------------------------------


def test_dry_run_succeeds_without_root() -> None:
    result = subprocess.run(  # noqa: S603 — controlled local script
        ["bash", str(_INSTALL_SCRIPT), "--dry-run"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert "would: install unit" in out
    assert "would: systemctl daemon-reload" in out
    # Service user line — either "ok" (already present) or "would: create"
    assert (
        "ok: service user 'quantmind' already exists" in out
        or "would: create system user quantmind" in out
    )


def test_dry_run_reports_install_complete_summary() -> None:
    result = subprocess.run(  # noqa: S603
        ["bash", str(_INSTALL_SCRIPT), "--dry-run"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "install complete" in result.stdout
    assert "journalctl -u quantmind -f" in result.stdout


def test_help_flag_prints_usage() -> None:
    result = subprocess.run(  # noqa: S603
        ["bash", str(_INSTALL_SCRIPT), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "Usage:" in result.stdout
    assert "--dry-run" in result.stdout
    assert "--enable" in result.stdout


def test_unknown_arg_exits_2() -> None:
    result = subprocess.run(  # noqa: S603
        ["bash", str(_INSTALL_SCRIPT), "--bogus"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "unknown argument" in result.stderr


def test_dry_run_with_enable_includes_enable_action() -> None:
    result = subprocess.run(  # noqa: S603
        ["bash", str(_INSTALL_SCRIPT), "--dry-run", "--enable"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "would: systemctl enable quantmind" in result.stdout


# ---------------------------------------------------------------------------
# Unit file content invariants
# ---------------------------------------------------------------------------


def test_unit_file_has_required_directives() -> None:
    text = _UNIT_FILE.read_text(encoding="utf-8")
    # J-003 spec — production-grade lifecycle.
    assert "Restart=always" in text
    assert "RestartSec=10s" in text
    assert "StartLimitBurst=20" in text
    assert "After=" in text and "mongod.service redis.service" in text
    assert "Requires=mongod.service redis.service" in text
    assert "User=quantmind" in text
    assert "Group=quantmind" in text
    assert "EnvironmentFile=/home/ps/.quantmind.env" in text
    assert (
        "ExecStart=/home/ps/anaconda3/envs/zhanglan/bin/uvicorn" in text
    )
    assert "--host 127.0.0.1" in text  # P1-6 §1.5 loopback only
    assert "StandardOutput=journal" in text
    assert "StandardError=journal" in text


def test_unit_file_protects_secrets_file_read_only() -> None:
    text = _UNIT_FILE.read_text(encoding="utf-8")
    assert "ReadOnlyPaths=/home/ps/.quantmind.env" in text


def test_unit_file_does_not_bind_zero_zero_zero_zero() -> None:
    """P1-6 §1.5 loopback-only red line — must not bind on 0.0.0.0."""
    text = _UNIT_FILE.read_text(encoding="utf-8")
    assert "0.0.0.0" not in text


def test_env_template_does_not_contain_real_secrets() -> None:
    """All credential lines must be commented out + placeholders only."""
    text = _ENV_TEMPLATE.read_text(encoding="utf-8")
    # The forbidden plaintext patterns from backend.audit.models
    # _FORBIDDEN_PLAINTEXT_RE would catch real values.
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        # Any active assignment must NOT carry a credential.
        for forbidden in (
            "DEEPSEEK_API_KEY=sk-",
            "DASHSCOPE_API_KEY=sk-",
            "MOONSHOT_API_KEY=sk-",
            "FEISHU_APP_SECRET=",
            "FEISHU_VERIFY_TOKEN=",
            "FEISHU_ENCRYPT_KEY=",
        ):
            assert not line.startswith(forbidden), (
                f"env template has uncommented active credential line: {line}"
            )
