"""為每個目標建立獨立的 detached Git worktree。"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

GIT_TIMEOUT_SECONDS = 120
WORKTREE_DIRECTORY = "unit-test-worktrees"

_ACTIVE_PROCESS: subprocess.Popen[str] | None = None
_CANCEL_REQUESTED = False


class RequestError(RuntimeError):
    """可安全回傳給代理的輸入或環境錯誤。"""


def terminate_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=5)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def run_command(
    command: list[str],
    *,
    cwd: Path,
    allow_cancelled: bool = False,
) -> subprocess.CompletedProcess[str]:
    global _ACTIVE_PROCESS
    if _CANCEL_REQUESTED and not allow_cancelled:
        return subprocess.CompletedProcess(command, 130, "", "工作已取消")
    try:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            start_new_session=True,
        )
    except FileNotFoundError as exc:
        raise RequestError(f"找不到必要指令：{command[0]}") from exc
    _ACTIVE_PROCESS = process
    try:
        try:
            stdout, stderr = process.communicate(timeout=GIT_TIMEOUT_SECONDS)
            return_code = process.returncode
        except subprocess.TimeoutExpired:
            terminate_process(process)
            stdout, stderr = process.communicate()
            return_code = 124
        if _CANCEL_REQUESTED and not allow_cancelled:
            return_code = 130
            stderr = (stderr + "\n工作已取消").lstrip("\n")
        return subprocess.CompletedProcess(command, return_code, stdout, stderr)
    finally:
        if _ACTIVE_PROCESS is process:
            _ACTIVE_PROCESS = None


def request_cancellation(_signum: int, _frame: object | None) -> None:
    global _CANCEL_REQUESTED
    _CANCEL_REQUESTED = True
    if _ACTIVE_PROCESS is not None:
        terminate_process(_ACTIVE_PROCESS)


def command_failure(
    result: subprocess.CompletedProcess[str], message: str
) -> RequestError:
    detail = (result.stdout + result.stderr).strip()[-4_000:]
    return RequestError(message + (f"：{detail}" if detail else ""))


def git(repo: Path, *arguments: str, message: str = "Git 指令失敗") -> str:
    result = run_command(["git", "-C", str(repo), *arguments], cwd=repo)
    if result.returncode != 0:
        raise command_failure(result, message)
    return result.stdout.strip()


def repo_root(value: str) -> Path:
    repo = Path(value).resolve()
    if not repo.is_dir():
        raise RequestError("專案根目錄不存在")
    if not (repo / "pom.xml").is_file():
        raise RequestError("專案根目錄缺少 pom.xml")
    wrapper = repo / "mvnw"
    if not wrapper.is_file() or not os.access(wrapper, os.X_OK):
        raise RequestError("專案根目錄需要可執行的 mvnw")
    return repo


def read_targets() -> list[str]:
    targets = [target["target_class"] for target in json.load(sys.stdin)["targets"]]
    if len(targets) != len(set(targets)):
        raise RequestError("target_class 不得重複")
    return targets


def worktree_path(repo: Path, target_class: str) -> tuple[Path, str]:
    simple_name = target_class.rsplit(".", 1)[-1]
    slug = re.sub(r"[^a-z0-9]+", "-", simple_name.lower()).strip("-") or "test"
    relative = f"{WORKTREE_DIRECTORY}/{slug}-{uuid.uuid4()}"
    return repo / relative, relative


def remove_worktree(repo: Path, worktree: Path) -> None:
    run_command(
        ["git", "-C", str(repo), "worktree", "remove", "--force", str(worktree)],
        cwd=repo,
        allow_cancelled=True,
    )
    if worktree.exists():
        shutil.rmtree(worktree, ignore_errors=True)


def prepare(repo: Path, targets: list[str]) -> dict[str, Any]:
    status = git(repo, "status", "--porcelain=v1", "--untracked-files=all")
    if status:
        preview = "；".join(status.splitlines()[:5])
        raise RequestError(f"建立工作樹前，主工作目錄必須乾淨：{preview}")
    ignored = run_command(
        ["git", "-C", str(repo), "check-ignore", "-q", f"{WORKTREE_DIRECTORY}/example"],
        cwd=repo,
    )
    if ignored.returncode != 0:
        raise RequestError(f".gitignore 必須排除 {WORKTREE_DIRECTORY}/")

    root = repo / WORKTREE_DIRECTORY
    planned = [
        (target_class, *worktree_path(repo, target_class))
        for target_class in targets
    ]
    if planned:
        root.mkdir(parents=True, exist_ok=True)

    created: list[Path] = []
    try:
        for target_class, worktree, relative in planned:
            result = run_command(
                [
                    "git",
                    "-C",
                    str(repo),
                    "worktree",
                    "add",
                    "--detach",
                    "--quiet",
                    str(worktree),
                    "HEAD",
                ],
                cwd=repo,
            )
            if result.returncode != 0:
                remove_worktree(repo, worktree)
                raise command_failure(result, f"無法建立 {target_class} 的工作樹")
            created.append(worktree)
    except Exception:
        for worktree in reversed(created):
            remove_worktree(repo, worktree)
        raise

    return {
        "worktrees": [
            {"target_class": target_class, "worktree": relative}
            for target_class, _, relative in planned
        ],
    }


def main() -> int:
    signal.signal(signal.SIGTERM, request_cancellation)
    signal.signal(signal.SIGINT, request_cancellation)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".")
    args = parser.parse_args()
    try:
        result = prepare(repo_root(args.repo), read_targets())
        successful = True
    except (RequestError, OSError, UnicodeError) as exc:
        result = {
            "status": "cancelled" if _CANCEL_REQUESTED else "preparation-failed",
            "message": str(exc),
        }
        successful = False
    except Exception as exc:  # noqa: BLE001 - CLI 邊界需回傳結構化錯誤
        result = {"status": "internal-error", "message": str(exc)}
        successful = False
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if successful else 3


if __name__ == "__main__":
    raise SystemExit(main())
