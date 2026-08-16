# /// script
# requires-python = ">=3.11"
# ///

"""建立 Javadoc 專用分支、worktree 與 Java 檔案清單。"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path, PurePosixPath
from typing import Any


class PrepareError(RuntimeError):
    pass


def command(arguments: list[str], *, cwd: Path, message: str) -> str:
    try:
        result = subprocess.run(
            arguments,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            check=False,
        )
    except FileNotFoundError as error:
        raise PrepareError(f"找不到必要指令：{arguments[0]}") from error
    except subprocess.TimeoutExpired as error:
        raise PrepareError(f"指令逾時：{' '.join(arguments)}") from error
    if result.returncode != 0:
        detail = (result.stdout + result.stderr).strip()[-4_000:]
        raise PrepareError(message + (f"：{detail}" if detail else ""))
    return result.stdout.strip()


def git(repo: Path, *arguments: str, message: str = "Git 指令失敗") -> str:
    return command(["git", "-C", str(repo), *arguments], cwd=repo, message=message)


def normalize_path(value: str) -> str:
    raw = value.strip().removeprefix("@")
    relative = PurePosixPath(raw)
    if (
        "\\" in raw
        or relative.is_absolute()
        or any(part in ("", ".", "..") for part in relative.parts)
    ):
        raise PrepareError("必須使用專案相對的正斜線路徑")
    return relative.as_posix()


def standard_java_path(path: str) -> bool:
    parts = PurePosixPath(path).parts
    return path.endswith(".java") and any(
        parts[index : index + 3] == ("src", "main", "java")
        for index in range(len(parts) - 2)
    )


def java_files(root: Path, target_path: str | None = None) -> list[str]:
    files = sorted(
        path
        for path in git(root, "ls-files", message="無法列出 Git 追蹤檔案").splitlines()
        if standard_java_path(path)
        and (root / path).is_file()
        and not (root / path).is_symlink()
    )
    if target_path is None:
        return files
    target = normalize_path(target_path)
    if target not in files:
        raise PrepareError(f"指定檔案不是 Git 追蹤的 Maven 正式 Java 檔案：{target}")
    return [target]


def state_path(repo: Path, run_id: str) -> Path:
    common = Path(git(repo, "rev-parse", "--git-common-dir"))
    if not common.is_absolute():
        common = repo / common
    return common.resolve() / "opencode-javadoc" / f"{run_id}.json"


def save_state(repo: Path, state: dict[str, Any]) -> None:
    destination = state_path(repo, state["run_id"])
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(dir=destination.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(state, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def remote_default_branch(repo: Path) -> tuple[str, str]:
    git(repo, "fetch", "--prune", "origin", message="無法更新 origin")
    reference = git(
        repo,
        "ls-remote",
        "--symref",
        "origin",
        "HEAD",
        message="無法查詢 origin 的遠端預設分支",
    )
    match = re.search(r"(?m)^ref:\s+refs/heads/(\S+)\s+HEAD$", reference)
    if match is None:
        raise PrepareError("無法辨識 origin 的遠端預設分支")
    branch = match.group(1)
    if not re.fullmatch(r"[A-Za-z0-9._/-]+", branch):
        raise PrepareError("遠端預設分支名稱不合法")
    return branch, git(repo, "rev-parse", f"origin/{branch}")


def prepare(repo: Path, payload: dict[str, Any]) -> dict[str, Any]:
    if not (repo / ".git").exists() or not (repo / "pom.xml").is_file():
        raise PrepareError("目前工作目錄必須是含根 pom.xml 的 Git 專案")
    target = payload.get("target_path")
    if target is not None and not isinstance(target, str):
        raise PrepareError("target_path 必須是字串")

    default_branch, base_sha = remote_default_branch(repo)
    run_id = str(uuid.uuid4())
    relative = f"javadoc-worktrees/{run_id}"
    worktree = repo / relative
    branch = f"opencode/javadoc/{run_id}"
    worktree.parent.mkdir(parents=True, exist_ok=True)
    git(
        repo,
        "worktree",
        "add",
        "-b",
        branch,
        str(worktree),
        base_sha,
        message="無法建立 Javadoc worktree",
    )
    try:
        files = java_files(worktree, target)
        state = {
            "version": 2,
            "run_id": run_id,
            "worktree": relative,
            "branch": branch,
            "default_branch": default_branch,
            "base_sha": base_sha,
            "status": "prepared",
            "files": files,
            "validation": None,
        }
        save_state(repo, state)
    except Exception:
        git(
            repo,
            "worktree",
            "remove",
            "--force",
            str(worktree),
            message="準備失敗且無法清理 worktree",
        )
        git(repo, "branch", "-D", branch, message="準備失敗且無法清理分支")
        raise
    return {
        "status": "prepared",
        "worktree": relative,
        "branch": branch,
        "base_branch": default_branch,
        "base_sha": base_sha,
        "files": [{"path": path} for path in files],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True, type=Path)
    arguments = parser.parse_args()
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise PrepareError("輸入必須是 JSON 物件")
        result = prepare(arguments.repo.resolve(), payload)
    except (PrepareError, json.JSONDecodeError, OSError) as error:
        result = {"status": "rejected", "message": str(error)}
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
