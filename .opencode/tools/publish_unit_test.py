"""提交已驗證的候選測試、推送分支並建立 Draft PR。"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

GIT_TIMEOUT_SECONDS = 120
GITHUB_TIMEOUT_SECONDS = 120
WORKTREE_DIRECTORY = "unit-test-worktrees"
BRANCH_PREFIX = "opencode/unit-test"

JAVA_CLASS = re.compile(r"^[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)+$")
WORKTREE_NAME = re.compile(r"^[a-z0-9][a-z0-9-]*$")

_ACTIVE_PROCESS: subprocess.Popen[str] | None = None
_CANCEL_REQUESTED = False


class RequestError(RuntimeError):
    """可安全回傳給代理的輸入或環境錯誤。"""


@dataclass(frozen=True)
class Publication:
    worktree: Path
    relative_worktree: str
    target_class: str
    test_file: str
    remote: str
    base_branch: str
    branch: str


def cancelled() -> bool:
    return _CANCEL_REQUESTED


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


def request_cancellation(_signum: int, _frame: object | None) -> None:
    global _CANCEL_REQUESTED
    _CANCEL_REQUESTED = True
    if _ACTIVE_PROCESS is not None:
        terminate_process(_ACTIVE_PROCESS)


def install_signal_handlers() -> None:
    signal.signal(signal.SIGTERM, request_cancellation)
    signal.signal(signal.SIGINT, request_cancellation)


def run_command(
    command: list[str],
    *,
    cwd: Path,
    timeout: int,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    global _ACTIVE_PROCESS
    if cancelled():
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
            env=env,
            start_new_session=True,
        )
    except FileNotFoundError as exc:
        raise RequestError(f"找不到必要指令：{command[0]}") from exc
    _ACTIVE_PROCESS = process
    try:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
            return_code = process.returncode
        except subprocess.TimeoutExpired:
            terminate_process(process)
            stdout, stderr = process.communicate()
            return_code = 124
        if cancelled():
            return_code = 130
            stderr = (stderr + "\n工作已取消").lstrip("\n")
        return subprocess.CompletedProcess(command, return_code, stdout, stderr)
    finally:
        _ACTIVE_PROCESS = None


def command_failure(
    result: subprocess.CompletedProcess[str], message: str
) -> RequestError:
    detail = (result.stdout + result.stderr).strip()[-4_000:]
    return RequestError(message + (f"：{detail}" if detail else ""))


def checked_command(
    command: list[str],
    *,
    cwd: Path,
    timeout: int,
    message: str,
    env: dict[str, str] | None = None,
) -> str:
    result = run_command(command, cwd=cwd, timeout=timeout, env=env)
    if result.returncode != 0:
        raise command_failure(result, message)
    return result.stdout.strip()


def git(repo: Path, *arguments: str, message: str = "Git 指令失敗") -> str:
    return checked_command(
        ["git", "-C", str(repo), *arguments],
        cwd=repo,
        timeout=GIT_TIMEOUT_SECONDS,
        message=message,
    )


def read_input() -> dict[str, Any]:
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        raise RequestError(f"輸入不是有效 JSON：{exc}") from exc
    if not isinstance(data, dict):
        raise RequestError("輸入必須是 JSON 物件")
    return data


def required_string(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RequestError(f"缺少有效欄位：{key}")
    return value.strip()


def repo_root(value: str) -> Path:
    repo = Path(value).resolve()
    if repo != Path.cwd().resolve():
        raise RequestError("--repo 必須指向目前工作目錄")
    return repo


def candidate_path(target_class: str) -> PurePosixPath:
    package, _, simple_name = target_class.rpartition(".")
    return PurePosixPath(
        "src", "test", "java", *package.split("."), f"{simple_name}Test.java"
    )


def upstream(repo: Path) -> tuple[str, str]:
    value = git(
        repo,
        "rev-parse",
        "--abbrev-ref",
        "--symbolic-full-name",
        "@{upstream}",
        message="目前分支沒有 upstream，無法決定推送 remote 與 PR base",
    )
    remote, separator, base_branch = value.partition("/")
    if not separator or not remote or not base_branch:
        raise RequestError(f"無法解析目前分支 upstream：{value}")
    return remote, base_branch


def load_publication(repo: Path, data: dict[str, Any]) -> Publication:
    target_class = required_string(data, "target_class")
    if JAVA_CLASS.fullmatch(target_class) is None:
        raise RequestError(f"完整類別名稱格式無效：{target_class}")

    relative = PurePosixPath(required_string(data, "worktree"))
    if (
        relative.is_absolute()
        or len(relative.parts) != 2
        or relative.parts[0] != WORKTREE_DIRECTORY
        or WORKTREE_NAME.fullmatch(relative.parts[1]) is None
    ):
        raise RequestError("worktree 必須是 prepare 回傳的 unit-test-worktrees/<名稱>")
    unresolved = repo.joinpath(*relative.parts)
    if unresolved.is_symlink():
        raise RequestError("worktree 不得是符號連結")
    worktree = unresolved.resolve()
    if not worktree.is_dir():
        raise RequestError("worktree 不存在")

    remote, base_branch = upstream(repo)
    return Publication(
        worktree=worktree,
        relative_worktree=relative.as_posix(),
        target_class=target_class,
        test_file=candidate_path(target_class).as_posix(),
        remote=remote,
        base_branch=base_branch,
        branch=f"{BRANCH_PREFIX}/{relative.name}",
    )


def commit_candidate(publication: Publication) -> str:
    git(
        publication.worktree,
        "switch",
        "--quiet",
        "-c",
        publication.branch,
        message=f"無法建立發布分支 {publication.branch}",
    )
    git(
        publication.worktree,
        "add",
        "--",
        publication.test_file,
        message="無法暫存候選測試",
    )
    git(
        publication.worktree,
        "-c",
        "commit.gpgSign=false",
        "commit",
        "--quiet",
        "--only",
        "-m",
        f"新增 {publication.target_class} 單元測試",
        "--",
        publication.test_file,
        message="無法建立候選測試提交",
    )
    return git(publication.worktree, "rev-parse", "HEAD").lower()


def push_branch(publication: Publication) -> None:
    result = run_command(
        [
            "git",
            "-C",
            str(publication.worktree),
            "push",
            "--set-upstream",
            publication.remote,
            publication.branch,
        ],
        cwd=publication.worktree,
        timeout=GIT_TIMEOUT_SECONDS,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
    if result.returncode != 0:
        raise command_failure(result, f"無法推送分支 {publication.branch}")


def create_draft_pr(publication: Publication) -> str:
    body = (
        f"受測類別：`{publication.target_class}`\n\n"
        f"測試檔案：`{publication.test_file}`\n\n"
        "候選測試已由 validate_unit_test 完成本機驗證。\n"
    )
    result = run_command(
        [
            "gh",
            "pr",
            "create",
            "--draft",
            "--base",
            publication.base_branch,
            "--head",
            publication.branch,
            "--title",
            f"test: 新增 {publication.target_class} 單元測試",
            "--body",
            body,
        ],
        cwd=publication.worktree,
        timeout=GITHUB_TIMEOUT_SECONDS,
        env={
            **os.environ,
            "GH_PROMPT_DISABLED": "1",
            "GH_PAGER": "cat",
            "NO_COLOR": "1",
        },
    )
    if result.returncode != 0:
        raise command_failure(result, "無法建立 Draft PR")
    urls = re.findall(r"https?://[^\s]+", result.stdout)
    if not urls:
        raise RequestError("gh pr create 沒有回傳 PR URL")
    return urls[-1].rstrip(".,)")


def publish(publication: Publication) -> dict[str, Any]:
    commit_sha = commit_candidate(publication)
    push_branch(publication)
    pr_url = create_draft_pr(publication)
    return {
        "status": "draft-pr-created",
        "target_class": publication.target_class,
        "test_file": publication.test_file,
        "worktree": publication.relative_worktree,
        "branch": publication.branch,
        "commit_sha": commit_sha,
        "pr_url": pr_url,
    }


def main() -> int:
    install_signal_handlers()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".")
    args = parser.parse_args()
    publication: Publication | None = None
    try:
        repo = repo_root(args.repo)
        publication = load_publication(repo, read_input())
        result = publish(publication)
        successful = True
    except (RequestError, OSError, UnicodeError) as exc:
        result: dict[str, Any] = {
            "status": "cancelled" if cancelled() else "publication-failed",
            "message": str(exc),
        }
        if publication is not None:
            result["worktree"] = publication.relative_worktree
        successful = False
    except Exception as exc:  # noqa: BLE001 - CLI 邊界必須回傳有效 JSON
        result = {"status": "internal-error", "message": str(exc)}
        successful = False
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if successful else 3


if __name__ == "__main__":
    raise SystemExit(main())
