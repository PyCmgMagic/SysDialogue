#!/usr/bin/env python3
"""Git preflight guard for repository development."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def run_git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    if check and result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode,
            result.args,
            output=result.stdout,
            stderr=result.stderr,
        )
    return result


def print_block(title: str, body: str) -> None:
    print(f"[{title}]")
    print(body.strip() or "(empty)")
    print()


def main() -> int:
    try:
        repo = run_git("rev-parse", "--show-toplevel").stdout.strip()
    except subprocess.CalledProcessError as exc:
        print_block("error", exc.stderr or "Not inside a git repository.")
        return 1

    expected_root = os.path.normcase(os.path.normpath(str(REPO_ROOT.resolve())))
    actual_root = os.path.normcase(os.path.normpath(str(Path(repo).resolve())))
    if actual_root != expected_root:
        print_block("error", f"Expected repo root: {expected_root}\nActual repo root:   {actual_root}")
        return 1

    status = run_git("status", "--short", "--branch").stdout.strip()
    dirty = bool(run_git("status", "--porcelain").stdout.strip())
    branch = run_git("branch", "--show-current").stdout.strip() or "(detached HEAD)"
    head = run_git("rev-parse", "--short", "HEAD").stdout.strip()

    print_block("repo", f"root:   {REPO_ROOT}\nbranch: {branch}\nhead:   {head}")
    print_block("status", status or "clean")

    fetch = run_git("fetch", "--all", "--prune", check=False)
    if fetch.returncode != 0:
        print_block("fetch failed", fetch.stderr or fetch.stdout or "git fetch failed.")
        return 3
    print_block("fetch", fetch.stderr or fetch.stdout or "git fetch --all --prune completed.")

    upstream_result = run_git(
        "rev-parse",
        "--abbrev-ref",
        "--symbolic-full-name",
        "@{u}",
        check=False,
    )
    upstream = upstream_result.stdout.strip() if upstream_result.returncode == 0 else ""

    if dirty:
        print_block(
            "blocked",
            "Worktree is not clean, so git pull --rebase was skipped.\n"
            "Commit or resolve local changes first, then rerun this preflight.",
        )
        return 2

    if not upstream:
        print_block("skip pull", "No upstream tracking branch is configured for the current branch.")
        return 0

    pull = run_git("pull", "--rebase", check=False)
    if pull.returncode != 0:
        print_block("pull failed", pull.stderr or pull.stdout or "git pull --rebase failed.")
        return 4

    final_status = run_git("status", "--short", "--branch").stdout.strip()
    print_block("pull", pull.stderr or pull.stdout or "git pull --rebase completed.")
    print_block("final status", final_status or "clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
