#!/usr/bin/env python3
"""保存 Git 工作樹基準，並稽核基準後是否只修改 Maven 測試目錄。"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any


DEFAULT_BASELINE = "target/unit-test-agent/baseline.json"


class GuardError(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    snapshot = subparsers.add_parser("snapshot", help="保存目前 Git 基準")
    snapshot.add_argument("--repo", default=".")
    snapshot.add_argument("--output", default=DEFAULT_BASELINE)

    audit = subparsers.add_parser("audit", help="稽核基準後的變更")
    audit.add_argument("--repo", default=".")
    audit.add_argument("--baseline", default=DEFAULT_BASELINE)
    audit.add_argument(
        "--allow",
        action="append",
        default=[],
        help="額外允許的相對路徑樣式；可重複指定",
    )
    return parser.parse_args()


def run_git_bytes(repo: Path, arguments: list[str]) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(repo), *arguments],
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", errors="replace").strip()
        raise GuardError(message or f"git {' '.join(arguments)} 執行失敗")
    return result.stdout


def run_git_text(repo: Path, arguments: list[str]) -> str:
    return run_git_bytes(repo, arguments).decode("utf-8", errors="replace").strip()


def require_current_repo(repo_argument: str) -> Path:
    repo = Path(repo_argument).resolve()
    if repo != Path.cwd().resolve():
        raise GuardError("--repo 必須指向目前工作目錄")
    root = Path(run_git_text(repo, ["rev-parse", "--show-toplevel"])).resolve()
    if root != repo:
        raise GuardError("請從 Git 工作樹根目錄執行")
    return root


def safe_baseline_path(repo: Path, value: str) -> Path:
    path = (repo / value).resolve() if not Path(value).is_absolute() else Path(value).resolve()
    allowed_root = (repo / "target/unit-test-agent").resolve()
    try:
        path.relative_to(allowed_root)
    except ValueError as exc:
        raise GuardError("基準檔案只能位於 target/unit-test-agent/**") from exc
    return path


def decode_path(value: bytes) -> str:
    return os.fsdecode(value).replace(os.sep, "/")


def parse_status(repo: Path) -> dict[str, dict[str, Any]]:
    raw = run_git_bytes(
        repo,
        ["status", "--porcelain=v2", "-z", "--untracked-files=all"],
    )
    tokens = raw.split(b"\0")
    entries: dict[str, dict[str, Any]] = {}
    index = 0
    while index < len(tokens):
        token = tokens[index]
        index += 1
        if not token:
            continue
        kind = chr(token[0])
        original_path: str | None = None
        if kind == "1":
            parts = token.split(b" ", 8)
            if len(parts) != 9:
                raise GuardError("無法解析 Git 一般狀態紀錄")
            path = decode_path(parts[8])
        elif kind == "2":
            parts = token.split(b" ", 9)
            if len(parts) != 10 or index >= len(tokens):
                raise GuardError("無法解析 Git 重新命名狀態紀錄")
            path = decode_path(parts[9])
            original_path = decode_path(tokens[index])
            index += 1
        elif kind == "u":
            parts = token.split(b" ", 10)
            if len(parts) != 11:
                raise GuardError("無法解析 Git 衝突狀態紀錄")
            path = decode_path(parts[10])
        elif kind in {"?", "!"}:
            path = decode_path(token[2:])
        elif kind == "#":
            continue
        else:
            raise GuardError(f"未知的 Git 狀態紀錄類型：{kind}")

        entries[path] = {
            "kind": kind,
            "record": token.decode("utf-8", errors="backslashreplace"),
            "original_path": original_path,
        }
    return entries


def path_fingerprint(repo: Path, relative_path: str) -> dict[str, str | None]:
    path = repo / PurePosixPath(relative_path)
    if path.is_symlink():
        target = os.readlink(path)
        digest = hashlib.sha256(f"symlink\0{target}".encode()).hexdigest()
        return {"kind": "symlink", "sha256": digest}
    if path.is_file():
        digest = hashlib.sha256()
        digest.update(f"mode:{path.stat().st_mode & 0o777:o}\0".encode())
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return {"kind": "file", "sha256": digest.hexdigest()}
    if path.is_dir():
        return {"kind": "directory", "sha256": None}
    return {"kind": "missing", "sha256": None}


def status_with_fingerprints(repo: Path) -> dict[str, dict[str, Any]]:
    entries = parse_status(repo)
    for path, entry in entries.items():
        entry["fingerprint"] = path_fingerprint(repo, path)
    return entries


def is_default_test_path(path: str) -> bool:
    parts = PurePosixPath(path).parts
    for index in range(len(parts) - 2):
        if parts[index] == "src" and parts[index + 1] == "test":
            return True
    return False


def is_allowed(path: str, extra_patterns: list[str]) -> bool:
    return is_default_test_path(path) or any(
        fnmatch.fnmatchcase(path, pattern) for pattern in extra_patterns
    )


def write_snapshot(repo: Path, output: Path) -> dict[str, Any]:
    head = run_git_text(repo, ["rev-parse", "HEAD"])
    entries = status_with_fingerprints(repo)
    relative_output = output.relative_to(repo).as_posix()
    snapshot = {
        "schema_version": 1,
        "repo": str(repo),
        "head": head,
        "baseline_file": relative_output,
        "entries": entries,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(snapshot, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    return {
        "status": "snapshot-created",
        "baseline": relative_output,
        "head": head,
        "preexisting_changes": sorted(entries),
    }


def load_snapshot(repo: Path, baseline: Path) -> dict[str, Any]:
    if not baseline.is_file():
        raise GuardError(f"找不到基準檔案：{baseline.relative_to(repo).as_posix()}")
    try:
        data = json.loads(baseline.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise GuardError(f"無法讀取基準檔案：{exc}") from exc
    if data.get("schema_version") != 1 or data.get("repo") != str(repo):
        raise GuardError("基準檔案與目前工作樹不相符")
    return data


def audit(repo: Path, baseline_path: Path, extra_patterns: list[str]) -> dict[str, Any]:
    baseline = load_snapshot(repo, baseline_path)
    current_head = run_git_text(repo, ["rev-parse", "HEAD"])
    current_entries = status_with_fingerprints(repo)
    baseline_entries: dict[str, dict[str, Any]] = baseline["entries"]
    runtime_path = baseline["baseline_file"]

    changed_since_baseline: set[str] = set()
    for path in set(baseline_entries) | set(current_entries):
        before = baseline_entries.get(path)
        after = current_entries.get(path)
        if before is None or after is None:
            if before is None:
                changed_since_baseline.add(path)
                continue
            current_fingerprint = path_fingerprint(repo, path)
            if current_fingerprint != before.get("fingerprint") or after is None:
                changed_since_baseline.add(path)
            continue
        if (
            before.get("record") != after.get("record")
            or before.get("original_path") != after.get("original_path")
            or before.get("fingerprint") != after.get("fingerprint")
        ):
            changed_since_baseline.add(path)

    changed_since_baseline.discard(runtime_path)
    allowed_changes = sorted(
        path for path in changed_since_baseline if is_allowed(path, extra_patterns)
    )
    violations = sorted(
        path for path in changed_since_baseline if not is_allowed(path, extra_patterns)
    )
    head_changed = current_head != baseline["head"]
    return {
        "status": "passed" if not violations and not head_changed else "blocked",
        "baseline_head": baseline["head"],
        "current_head": current_head,
        "head_changed": head_changed,
        "preexisting_changes": sorted(baseline_entries),
        "allowed_changes": allowed_changes,
        "violations": violations,
        "ignored_runtime_artifact": runtime_path,
    }


def main() -> int:
    args = parse_args()
    try:
        repo = require_current_repo(args.repo)
        if args.command == "snapshot":
            output = safe_baseline_path(repo, args.output)
            report = write_snapshot(repo, output)
            exit_code = 0
        else:
            baseline = safe_baseline_path(repo, args.baseline)
            report = audit(repo, baseline, args.allow)
            exit_code = 0 if report["status"] == "passed" else 3
    except GuardError as exc:
        report = {"status": "blocked", "error": str(exc)}
        exit_code = 2

    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)

