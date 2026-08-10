"""發布最新驗證通過的候選測試：提交、推送並建立 Draft PR。"""

from __future__ import annotations

import argparse
import json
import os
import re
from typing import Any

from _unit_test_common import (
    GIT_TIMEOUT_SECONDS,
    GITHUB_TIMEOUT_SECONDS,
    Assignment,
    RequestError,
    cancelled,
    candidate_snapshot,
    command_failure,
    git,
    github_environment,
    install_signal_handlers,
    load_assignment,
    parse_required_string,
    read_input,
    repo_root,
    run_command,
    validate_session_id,
)


def github_locator(assignment: Assignment) -> str:
    base = assignment.base
    if base.github_host == "github.com":
        return base.github_repository
    return f"{base.github_host}/{base.github_repository}"


def validated_digest(assignment: Assignment, validation_id: str) -> str:
    receipt = assignment.state.get("validation")
    if not isinstance(receipt, dict) or receipt.get("validation_id") != validation_id:
        raise RequestError("validation_id 不是目前候選內容的最新驗證憑證")
    digest = receipt.get("candidate_sha256")
    if not isinstance(digest, str):
        raise RequestError("驗證憑證缺少候選內容雜湊")
    return digest


def commit_candidate(assignment: Assignment, expected_digest: str) -> str:
    snapshot = candidate_snapshot(assignment, {}, require_cases=False)
    if snapshot["sha256"] != expected_digest:
        raise RequestError("候選測試在驗證通過後又被修改，請重新驗證")
    git(
        assignment.worktree,
        "add",
        "--",
        assignment.test_file,
        message="無法暫存候選測試",
    )
    git(
        assignment.worktree,
        "-c",
        "commit.gpgSign=false",
        "commit",
        "--quiet",
        "-m",
        f"新增 {assignment.target_class} 單元測試",
        message="無法建立候選測試提交",
    )
    return git(assignment.worktree, "rev-parse", "HEAD").lower()


def push_branch(assignment: Assignment) -> None:
    result = run_command(
        [
            "git",
            "-C",
            str(assignment.worktree),
            "push",
            "--porcelain",
            assignment.base.remote,
            f"HEAD:refs/heads/{assignment.branch}",
        ],
        cwd=assignment.worktree,
        timeout=GIT_TIMEOUT_SECONDS,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
    if result.returncode != 0:
        raise command_failure(result, f"無法推送分支 {assignment.branch}")


def create_draft_pr(assignment: Assignment, validation_id: str) -> str:
    body = (
        f"受測類別：`{assignment.target_class}`\n\n"
        f"測試檔案：`{assignment.test_file}`\n\n"
        f"本機驗證編號：`{validation_id}`\n"
    )
    result = run_command(
        [
            "gh",
            "pr",
            "create",
            "--draft",
            "--repo",
            github_locator(assignment),
            "--base",
            assignment.base.remote_branch,
            "--head",
            assignment.branch,
            "--title",
            f"test: 新增 {assignment.target_class} 單元測試",
            "--body",
            body,
        ],
        cwd=assignment.worktree,
        timeout=GITHUB_TIMEOUT_SECONDS,
        env=github_environment(),
    )
    if result.returncode != 0:
        raise command_failure(result, "無法建立 Draft PR")
    urls = re.findall(r"https?://[^\s]+", result.stdout)
    if not urls:
        raise RequestError("gh pr create 沒有回傳 PR URL")
    return urls[-1].rstrip(".,)")


def publish(assignment: Assignment, validation_id: str) -> dict[str, Any]:
    digest = validated_digest(assignment, validation_id)
    commit_sha = commit_candidate(assignment, digest)
    push_branch(assignment)
    pr_url = create_draft_pr(assignment, validation_id)
    worktree = str(assignment.worktree.relative_to(assignment.coordinator_repo))
    result = {
        "status": "draft-pr-created",
        "target_class": assignment.target_class,
        "branch": assignment.branch,
        "commit_sha": commit_sha,
        "pr_url": pr_url,
        "worktree": worktree,
        "worktree_retained": True,
    }
    return result


def main() -> int:
    install_signal_handlers()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".")
    parser.add_argument("--session-id", required=True)
    args = parser.parse_args()
    assignment: Assignment | None = None
    try:
        repo = repo_root(args.repo)
        session_id = validate_session_id(args.session_id)
        data = read_input()
        assignment = load_assignment(
            repo,
            parse_required_string(data, "assignment_id"),
            session_id,
            bind_worker=False,
        )
        result = publish(assignment, parse_required_string(data, "validation_id"))
        successful = True
    except (RequestError, OSError, UnicodeError) as exc:
        result = {
            "status": "cancelled" if cancelled() else "publication-failed",
            "message": str(exc),
        }
        if assignment is not None:
            result["worktree"] = str(
                assignment.worktree.relative_to(assignment.coordinator_repo)
            )
            result["worktree_retained"] = True
        successful = False
    except Exception as exc:  # noqa: BLE001 - CLI 邊界必須回傳有效 JSON
        result = {"status": "internal-error", "message": str(exc)}
        successful = False
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if successful else 3


if __name__ == "__main__":
    raise SystemExit(main())
