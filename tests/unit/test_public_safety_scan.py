"""Tests for scripts/public_safety_scan.py.

Planted violations are built by string concatenation so this test file itself
never contains a denylisted literal (the scanner scans tests/ too).
"""

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "public_safety_scan.py"
REPO_CONFIG = REPO_ROOT / "config" / "safety.yaml"


def run_scan(root: Path, config: Path = REPO_CONFIG) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), "--config", str(config)],
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture
def clean_dir(tmp_path):
    (tmp_path / "notes.md").write_text("Nothing sensitive here.\n", encoding="utf-8")
    return tmp_path


def test_clean_directory_passes(clean_dir):
    result = run_scan(clean_dir)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK" in result.stdout


def test_detects_secret_shaped_token(clean_dir):
    planted = "AKIA" + "X" * 16
    (clean_dir / "leak.txt").write_text(f"key = {planted}\n", encoding="utf-8")
    result = run_scan(clean_dir)
    assert result.returncode == 1
    assert "aws-access-key" in result.stdout
    assert "leak.txt:1" in result.stdout


def test_detects_personal_email_but_allows_placeholder(clean_dir):
    personal = "someone" + "@" + "gmail" + ".com"
    (clean_dir / "contact.md").write_text(f"reach me at {personal} or docs@example.com\n", encoding="utf-8")
    result = run_scan(clean_dir)
    assert result.returncode == 1
    assert result.stdout.count("email-address") == 1
    assert "example.com" not in result.stdout


def test_detects_employer_phrase(clean_dir):
    phrase = "my " + "company"
    (clean_dir / "blog.md").write_text(f"At {phrase} we deployed this widely.\n", encoding="utf-8")
    result = run_scan(clean_dir)
    assert result.returncode == 1
    assert "employer-reference" in result.stdout


def test_detects_self_praise(clean_dir):
    phrase = "state-of-the-" + "art"
    (clean_dir / "pitch.md").write_text(f"A {phrase} platform.\n", encoding="utf-8")
    result = run_scan(clean_dir)
    assert result.returncode == 1
    assert "self-praise" in result.stdout


@pytest.mark.parametrize(
    "phrase",
    [
        "during my " + "time at",
        "in my " + "role as a platform engineer",
        "my previous " + "employer",
        "my work " + "experience",
        "my " + "career",
    ],
)
def test_detects_personal_history(clean_dir, phrase):
    """The repo documents code, never a person's biography or work history."""
    (clean_dir / "about.md").write_text(f"Some prose {phrase} and more prose.\n", encoding="utf-8")
    result = run_scan(clean_dir)
    assert result.returncode == 1, result.stdout
    assert "personal-history" in result.stdout


@pytest.mark.parametrize(
    "phrase",
    [
        "hiring " + "manager",
        "recrui" + "ter",
        "job " + "market",
        "job " + "application",
        "open " + "to work",
        "portfolio " + "piece",
        "hire " + "me",
    ],
)
def test_detects_career_language(clean_dir, phrase):
    """This is a technical artifact for the community, never a career document."""
    (clean_dir / "notes.md").write_text(f"Text mentioning a {phrase} here.\n", encoding="utf-8")
    result = run_scan(clean_dir)
    assert result.returncode == 1, result.stdout
    assert "career-or-hiring-language" in result.stdout


@pytest.mark.parametrize(
    "sentence",
    [
        "The job of the supervisor is to plan and route sub-tasks.",
        "Resume the run from the last checkpoint after a failure.",
        "Each specialist agent is scoped to one job rather than many.",
        "We built this chunker to respect section boundaries.",
        "The pipeline resumes cleanly because ingest is idempotent.",
        "Career-independent naming keeps the module vocabulary neutral.",
    ],
)
def test_ordinary_engineering_prose_is_not_flagged(clean_dir, sentence):
    """Guard against over-broad rules: these are legitimate sentences that must pass.

    ``job`` and ``resume`` are ordinary engineering words and appear throughout this repo, so the
    career rule matches specific multi-word phrases rather than those bare terms.
    """
    (clean_dir / "docs.md").write_text(sentence + "\n", encoding="utf-8")
    result = run_scan(clean_dir)
    assert result.returncode == 0, result.stdout


def test_exclusions_respected(tmp_path):
    planted = "AKIA" + "Y" * 16
    (tmp_path / "PLAN.md").write_text(f"example bad token: {planted}\n", encoding="utf-8")
    result = run_scan(tmp_path)
    assert result.returncode == 0, result.stdout


def test_missing_config_is_an_error(tmp_path):
    result = run_scan(tmp_path, config=tmp_path / "nope.yaml")
    assert result.returncode == 2
    assert "cannot load config" in result.stderr
