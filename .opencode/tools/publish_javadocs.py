# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "tree-sitter==0.26.0",
#   "tree-sitter-java==0.23.5",
# ]
# ///

"""以可替換發布介面提交、推送並建立 GitHub Draft PR。"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path, PurePosixPath
from typing import Any


class JavadocError(RuntimeError):
    pass


def checked_command(
    arguments: list[str], *, cwd: Path, message: str, timeout: int = 120
) -> str:
    try:
        result = subprocess.run(
            arguments,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as error:
        raise JavadocError(f"找不到必要指令：{arguments[0]}") from error
    except subprocess.TimeoutExpired as error:
        raise JavadocError(f"指令逾時：{' '.join(arguments)}") from error
    if result.returncode != 0:
        detail = (result.stdout + result.stderr).strip()[-4_000:]
        raise JavadocError(message + (f"：{detail}" if detail else ""))
    return result.stdout.strip()


def git(repo: Path, *arguments: str, message: str = "Git 指令失敗") -> str:
    return checked_command(
        ["git", "-C", str(repo), *arguments], cwd=repo, message=message
    )


def normalize_path(value: str) -> str:
    relative = PurePosixPath(value.strip())
    if (
        relative.is_absolute()
        or any(part in ("", ".", "..") for part in relative.parts)
        or "\\" in value
    ):
        raise JavadocError("worktree 路徑不合法")
    return relative.as_posix()


def state_file(repo: Path, worktree: str) -> Path:
    parts = PurePosixPath(normalize_path(worktree)).parts
    if len(parts) != 2 or parts[0] != "javadoc-worktrees":
        raise JavadocError("worktree 格式不正確")
    common = Path(git(repo, "rev-parse", "--git-common-dir"))
    if not common.is_absolute():
        common = repo / common
    return common.resolve() / "opencode-javadoc" / f"{parts[1]}.json"


def load_state(repo: Path, worktree: str) -> dict[str, Any]:
    path = state_file(repo, worktree)
    if not path.is_file():
        raise JavadocError("找不到 Javadoc 執行狀態")
    state = json.loads(path.read_text(encoding="utf-8"))
    if state.get("worktree") != normalize_path(worktree):
        raise JavadocError("執行狀態與 worktree 不一致")
    return state


def save_state(repo: Path, state: dict[str, Any]) -> None:
    destination = state_file(repo, state["worktree"])
    descriptor, temporary_name = tempfile.mkstemp(dir=destination.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(state, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def resolve_worktree(repo: Path, relative: str) -> Path:
    worktree = repo.joinpath(*PurePosixPath(relative).parts).resolve()
    if worktree.parent != (repo / "javadoc-worktrees").resolve() or not worktree.is_dir():
        raise JavadocError("Javadoc worktree 不存在或路徑不合法")
    return worktree


class PullRequestPublisher(ABC):
    @abstractmethod
    def create_draft(
        self, *, worktree: Path, base: str, head: str, title: str, body: str
    ) -> str:
        """建立 Draft PR 並回傳網址。"""

    @abstractmethod
    def verify(self, *, worktree: Path, url: str, expected_sha: str) -> dict[str, Any]:
        """驗證 PR 狀態與分支提交。"""


class GitHubPublisher(PullRequestPublisher):
    def create_draft(
        self, *, worktree: Path, base: str, head: str, title: str, body: str
    ) -> str:
        output = checked_command(
            [
                "gh",
                "pr",
                "create",
                "--draft",
                "--base",
                base,
                "--head",
                head,
                "--title",
                title,
                "--body",
                body,
            ],
            cwd=worktree,
            timeout=180,
            message="無法建立 GitHub Draft PR",
        )
        match = re.search(r"https://\S+", output)
        if not match:
            raise JavadocError("GitHub 未回傳 Draft PR 網址")
        return match.group(0)

    def verify(self, *, worktree: Path, url: str, expected_sha: str) -> dict[str, Any]:
        output = checked_command(
            ["gh", "pr", "view", url, "--json", "url,isDraft,headRefOid"],
            cwd=worktree,
            timeout=120,
            message="無法驗證 GitHub Draft PR",
        )
        value = json.loads(output)
        if value.get("isDraft") is not True:
            raise JavadocError("建立的 PR 不是 Draft")
        if value.get("headRefOid") != expected_sha:
            raise JavadocError("Draft PR 的提交 SHA 與本機不一致")
        return value


def pr_body(state: dict[str, Any]) -> str:
    validation = state["validation"]
    changed = validation["changed_files"]
    failed = validation["failed_files"]
    blocked = validation["blocked_declarations"]
    lines = [
        "## 摘要",
        "",
        f"- 已變更 Java 檔案：{len(changed)}",
        f"- 未完成檔案：{len(failed)}",
        f"- 規格衝突宣告：{len(blocked)}",
        "",
        "## 驗證",
        "",
    ]
    lines.extend(f"- `{item['command']}`：通過" for item in validation["commands"])
    if failed:
        lines.extend(["", "## 未完成檔案", ""])
        lines.extend(f"- `{item['path']}`：{item['reason']}" for item in failed)
    if blocked:
        lines.extend(["", "## 規格與原始碼衝突", ""])
        lines.extend(
            f"- `{item['path']}` `{item['name']}`：{item['reason']}" for item in blocked
        )
    lines.extend(["", "此 PR 僅供人類審查；不會自動轉為 Ready 或合併。"])
    return "\n".join(lines)


def publish(repo: Path, payload: dict[str, Any]) -> dict[str, Any]:
    worktree_name = payload.get("worktree")
    publisher_name = payload.get("publisher", "github")
    if not isinstance(worktree_name, str):
        raise JavadocError("worktree 必須是字串")
    if publisher_name != "github":
        raise JavadocError("目前只實作 GitHub 發布介面")
    state = load_state(repo, worktree_name)
    if state.get("status") != "validated" or not isinstance(
        state.get("validation"), dict
    ):
        raise JavadocError("必須先通過 validate_javadocs")
    worktree = resolve_worktree(repo, worktree_name)
    changed = state["validation"]["changed_files"]

    actual = [
        line
        for line in git(
            worktree, "diff", "--name-only", state["base_sha"], "--"
        ).splitlines()
        if line
    ]
    if actual != changed:
        raise JavadocError("驗證後的檔案差異已改變")
    if not changed:
        state["status"] = "published"
        state["publication"] = {"status": "no-changes"}
        save_state(repo, state)
        git(
            repo,
            "worktree",
            "remove",
            str(worktree),
            message="無法清理無變更的 Javadoc worktree",
        )
        return {
            "status": "no-changes",
            "message": "沒有需要提交的 Javadoc 變更",
            "failed_files": state["validation"]["failed_files"],
        }

    git(worktree, "add", "--", *changed, message="無法暫存 Javadoc 檔案")
    staged = [
        line
        for line in git(worktree, "diff", "--cached", "--name-only", "--").splitlines()
        if line
    ]
    if staged != changed:
        raise JavadocError("暫存範圍與驗證結果不一致")
    git(
        worktree,
        "commit",
        "-m",
        "docs: refresh Javadocs",
        message="無法建立 Javadoc 提交",
    )
    commit_sha = git(worktree, "rev-parse", "HEAD")
    git(
        worktree,
        "push",
        "-u",
        "origin",
        state["branch"],
        message="無法推送 Javadoc 分支",
    )
    remote = git(
        worktree,
        "ls-remote",
        "origin",
        f"refs/heads/{state['branch']}",
        message="無法驗證遠端 Javadoc 分支",
    )
    remote_sha = remote.split()[0] if remote.split() else ""
    if remote_sha != commit_sha:
        raise JavadocError("遠端分支 SHA 與本機提交不一致")

    publisher = GitHubPublisher()
    url = publisher.create_draft(
        worktree=worktree,
        base=state["default_branch"],
        head=state["branch"],
        title="docs: refresh Javadocs",
        body=pr_body(state),
    )
    verified = publisher.verify(worktree=worktree, url=url, expected_sha=commit_sha)
    state["status"] = "published"
    state["publication"] = {
        "status": "published",
        "url": url,
        "commit_sha": commit_sha,
        "publisher": "github",
    }
    save_state(repo, state)
    cleanup_warning = None
    try:
        git(
            repo,
            "worktree",
            "remove",
            str(worktree),
            message="Draft PR 已建立，但無法清理 Javadoc worktree",
        )
    except JavadocError as error:
        cleanup_warning = str(error)
    return {
        "status": "published",
        "url": url,
        "branch": state["branch"],
        "commit_sha": commit_sha,
        "is_draft": verified["isDraft"],
        "changed_files": changed,
        "failed_files": state["validation"]["failed_files"],
        "blocked_declarations": state["validation"]["blocked_declarations"],
        "cleanup_warning": cleanup_warning,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True, type=Path)
    arguments = parser.parse_args()
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise JavadocError("輸入必須是 JSON 物件")
        result = publish(arguments.repo.resolve(), payload)
    except (JavadocError, json.JSONDecodeError, OSError) as error:
        result = {"status": "rejected", "message": str(error)}
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
