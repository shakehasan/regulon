#!/usr/bin/env python3
"""Public-safety scanner: fail the build if the repo contains denylisted content.

Enforces the public-safety Non-Negotiables from PLAN.md: no employer/client references,
no real personal data, no secret-shaped tokens, no banned self-praise vocabulary.
Patterns live in ``config/safety.yaml`` so the denylist is configurable without code changes.

Usage::

    python scripts/public_safety_scan.py [--root PATH] [--config PATH]

Exit code 0 means clean; 1 means violations were found (each printed as
``path:line: [pattern-name] matched text``); 2 means the scanner itself failed.

The scanner is intentionally dependency-light (stdlib + PyYAML) so it can run in a
bare CI job without installing the regulon package.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass(frozen=True)
class Pattern:
    """One denylist rule: a regex plus optional allow-regexes applied to each match."""

    name: str
    regex: re.Pattern[str]
    description: str
    allow: tuple[re.Pattern[str], ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ScanConfig:
    """Parsed scanner configuration."""

    patterns: tuple[Pattern, ...]
    exclude_paths: frozenset[str]
    exclude_dirs: frozenset[str]
    exclude_extensions: frozenset[str]


@dataclass(frozen=True)
class Violation:
    """A single denylist hit inside a scanned file."""

    path: Path
    line_no: int
    pattern_name: str
    matched_text: str


def load_config(config_path: Path) -> ScanConfig:
    """Load and validate the YAML denylist configuration."""
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Safety config {config_path} must be a mapping")

    patterns: list[Pattern] = []
    for entry in raw.get("patterns", []):
        if not isinstance(entry, dict) or "name" not in entry or "regex" not in entry:
            raise ValueError(f"Invalid pattern entry in {config_path}: {entry!r}")
        allow = tuple(re.compile(a) for a in entry.get("allow", []))
        patterns.append(
            Pattern(
                name=str(entry["name"]),
                regex=re.compile(str(entry["regex"])),
                description=str(entry.get("description", "")),
                allow=allow,
            )
        )
    if not patterns:
        raise ValueError(f"Safety config {config_path} defines no patterns")

    return ScanConfig(
        patterns=tuple(patterns),
        exclude_paths=frozenset(str(p) for p in raw.get("exclude_paths", [])),
        exclude_dirs=frozenset(str(d) for d in raw.get("exclude_dirs", [])),
        exclude_extensions=frozenset(str(e).lower() for e in raw.get("exclude_extensions", [])),
    )


def _git_files(root: Path) -> list[Path] | None:
    """List tracked plus untracked-but-not-ignored files via git, or None outside a repo."""
    try:
        proc = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return [root / line for line in proc.stdout.splitlines() if line.strip()]


def _walk_files(root: Path, config: ScanConfig) -> list[Path]:
    """Fallback file enumeration for non-git roots, honoring directory excludes."""
    files: list[Path] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel_parts = path.relative_to(root).parts
        if any(part in config.exclude_dirs for part in rel_parts[:-1]):
            continue
        files.append(path)
    return files


def iter_scannable_files(root: Path, config: ScanConfig) -> list[Path]:
    """Enumerate files to scan, applying path, directory, and extension excludes."""
    candidates = _git_files(root)
    if candidates is None:
        candidates = _walk_files(root, config)

    selected: list[Path] = []
    for path in candidates:
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if rel in config.exclude_paths:
            continue
        if any(part in config.exclude_dirs for part in path.relative_to(root).parts[:-1]):
            continue
        if path.suffix.lower() in config.exclude_extensions:
            continue
        selected.append(path)
    return selected


def scan_file(path: Path, config: ScanConfig) -> list[Violation]:
    """Scan one text file; binary files (undecodable or NUL-containing) are skipped."""
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return []
    if "\x00" in text:
        return []

    violations: list[Violation] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        for pattern in config.patterns:
            for match in pattern.regex.finditer(line):
                matched = match.group(0)
                if any(allow.search(matched) for allow in pattern.allow):
                    continue
                violations.append(
                    Violation(path=path, line_no=line_no, pattern_name=pattern.name, matched_text=matched)
                )
    return violations


def scan(root: Path, config: ScanConfig) -> list[Violation]:
    """Scan every eligible file under root and return all violations."""
    violations: list[Violation] = []
    for path in iter_scannable_files(root, config):
        violations.extend(scan_file(path, config))
    return violations


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint. Returns the process exit code."""
    repo_root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description="Scan the repo for public-safety violations.")
    parser.add_argument("--root", type=Path, default=repo_root, help="Directory to scan (default: repo root)")
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Denylist YAML (default: <root>/config/safety.yaml)",
    )
    args = parser.parse_args(argv)

    root: Path = args.root.resolve()
    config_path: Path = (args.config or root / "config" / "safety.yaml").resolve()

    try:
        config = load_config(config_path)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"public-safety-scan: cannot load config: {exc}", file=sys.stderr)
        return 2

    violations = scan(root, config)
    scanned = len(iter_scannable_files(root, config))

    if violations:
        for v in violations:
            rel = v.path.relative_to(root).as_posix()
            print(f"{rel}:{v.line_no}: [{v.pattern_name}] matched {v.matched_text!r}")
        print(f"\npublic-safety-scan: FAIL - {len(violations)} violation(s) across {scanned} file(s)")
        return 1

    print(f"public-safety-scan: OK - {scanned} file(s) clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
