"""M0 integration test: the repository itself satisfies its own governance gates."""

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

REQUIRED_FILES = [
    "PLAN.md",
    "README.md",
    "LICENSE",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "CODE_OF_CONDUCT.md",
    "ROADMAP.md",
    "AGENTS.md",
    "Makefile",
    "pyproject.toml",
    ".pre-commit-config.yaml",
    ".github/workflows/ci.yml",
    ".github/workflows/safety.yml",
    ".github/workflows/release.yml",
    ".github/PULL_REQUEST_TEMPLATE.md",
    "docs/adr/001-why-regulon.md",
    "docs/adr/002-local-first-real-inference.md",
    "scripts/public_safety_scan.py",
    "config/safety.yaml",
    "config/regulon.yaml",
]


@pytest.mark.integration
def test_required_governance_files_exist():
    missing = [f for f in REQUIRED_FILES if not (REPO_ROOT / f).is_file()]
    assert not missing, f"missing governance files: {missing}"


@pytest.mark.integration
def test_repo_passes_its_own_safety_scan():
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "public_safety_scan.py"), "--root", str(REPO_ROOT)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"safety scan failed:\n{result.stdout}\n{result.stderr}"


@pytest.mark.integration
def test_license_carries_author_name():
    text = (REPO_ROOT / "LICENSE").read_text(encoding="utf-8")
    assert "Shake MD Tareq Hasan" in text
    assert "MIT License" in text
