"""為每個目標建立獨立的 detached Git worktree。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import shutil
import subprocess
import sys
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
    top = git(repo, "rev-parse", "--show-toplevel", message="目前目錄不是 Git worktree")
    if Path(top).resolve() != repo:
        raise RequestError("--repo 必須指向 Git worktree 根目錄")
    return repo


def read_targets() -> list[str]:
    try:
        data = json.load(sys.stdin)
        raw_targets = data["targets"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise RequestError("輸入必須包含 targets 陣列") from exc
    if not isinstance(raw_targets, list):
        raise RequestError("targets 必須是陣列")
    targets: list[str] = []
    for raw in raw_targets:
        if not isinstance(raw, dict):
            raise RequestError("每個目標都必須是物件")
        target_class = raw.get("target_class")
        if not isinstance(target_class, str) or not target_class.strip():
            raise RequestError("每個目標都需要 target_class")
        targets.append(target_class.strip())
    if len(targets) != len(set(targets)):
        raise RequestError("target_class 不得重複")
    return targets


def worktree_path(repo: Path, session_id: str, target_class: str) -> tuple[Path, str]:
    simple_name = target_class.rsplit(".", 1)[-1]
    slug = re.sub(r"[^a-z0-9]+", "-", simple_name.lower()).strip("-") or "test"
    suffix = hashlib.sha256(
        f"{session_id}\0{target_class}".encode("utf-8")
    ).hexdigest()[:8]
    relative = f"{WORKTREE_DIRECTORY}/{slug}-{suffix}"
    return repo / relative, relative


def remove_worktree(repo: Path, worktree: Path) -> None:
    run_command(
        ["git", "-C", str(repo), "worktree", "remove", "--force", str(worktree)],
        cwd=repo,
        allow_cancelled=True,
    )
    if worktree.exists():
        shutil.rmtree(worktree, ignore_errors=True)


def prepare(repo: Path, session_id: str, targets: list[str]) -> dict[str, Any]:
    status = git(repo, "status", "--porcelain=v1", "--untracked-files=all")
    if status:
        preview = "；".join(status.splitlines()[:5])
        raise RequestError(f"建立工作樹前，主工作目錄必須乾淨：{preview}")
    base_sha = git(repo, "rev-parse", "HEAD", message="無法取得目前 Git 提交")
    ignored = run_command(
        ["git", "-C", str(repo), "check-ignore", "-q", f"{WORKTREE_DIRECTORY}/example"],
        cwd=repo,
    )
    if ignored.returncode != 0:
        raise RequestError(f".gitignore 必須排除 {WORKTREE_DIRECTORY}/")

    root = repo / WORKTREE_DIRECTORY
    if root.is_symlink():
        raise RequestError(f"{WORKTREE_DIRECTORY} 不得是符號連結")
    planned = [
        (target_class, *worktree_path(repo, session_id, target_class))
        for target_class in targets
    ]
    conflicts = [str(path) for _, path, _ in planned if path.exists()]
    if conflicts:
        raise RequestError("工作樹路徑已存在：" + "、".join(conflicts))
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
                    base_sha,
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
        "status": "prepared",
        "base_sha": base_sha,
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
    parser.add_argument("--session-id", required=True)
    args = parser.parse_args()
    try:
        result = prepare(repo_root(args.repo), args.session_id, read_targets())
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
