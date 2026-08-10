"""發布最新驗證通過的候選測試：提交、推送並建立 Draft PR。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from _unit_test_common import (
    GIT_TIMEOUT_SECONDS,
    GITHUB_TIMEOUT_SECONDS,
    MAX_PR_BODY_BYTES,
    Assignment,
    RequestError,
    cancelled,
    candidate_snapshot,
    changed_paths,
    command_failure,
    git,
    git_nul_paths,
    github_environment,
    install_signal_handlers,
    load_assignment,
    parse_required_string,
    read_input,
    remote_sha,
    repo_root,
    require_only_path,
    require_remote_sha,
    run_command,
    save_assignment_state,
    validate_session_id,
)


def github_locator(assignment: Assignment) -> str:
    base = assignment.base
    if base.github_host == "github.com":
        return base.github_repository
    return f"{base.github_host}/{base.github_repository}"


def validation_receipt(assignment: Assignment, validation_id: str) -> dict[str, Any]:
    receipt = assignment.state.get("validation")
    if not isinstance(receipt, dict):
        raise RequestError("尚未取得 validate_unit_test 的通過憑證")
    if receipt.get("validation_id") != validation_id:
        raise RequestError("validation_id 不是目前候選內容的最新驗證憑證")
    digest = receipt.get("candidate_sha256")
    cases = receipt.get("test_cases")
    result = receipt.get("result")
    if (
        not isinstance(digest, str)
        or re.fullmatch(r"[0-9a-f]{64}", digest) is None
        or not isinstance(cases, list)
        or not isinstance(result, dict)
    ):
        raise RequestError("驗證憑證內容不完整")
    candidate_tests = result.get("candidate_tests")
    coverage = result.get("coverage")
    if (
        not isinstance(candidate_tests, dict)
        or candidate_tests.get("executed", 0) <= 0
        or candidate_tests.get("skipped") != 0
        or candidate_tests.get("unexpected_classes") != []
        or not isinstance(coverage, dict)
        or coverage.get("passed") is not True
    ):
        raise RequestError("驗證憑證未達發布條件")
    return receipt


def committed_file_digest(project: Path, commit_sha: str, path: str) -> str:
    shown = run_command(
        ["git", "-C", str(project), "show", f"{commit_sha}:{path}"],
        cwd=project,
        timeout=GIT_TIMEOUT_SECONDS,
    )
    if shown.returncode != 0:
        raise command_failure(shown, "無法讀取已提交的候選測試")
    return hashlib.sha256(shown.stdout.encode("utf-8")).hexdigest()


def verify_candidate_commit(
    assignment: Assignment,
    commit_sha: str,
    expected_digest: str,
) -> None:
    project = assignment.worktree
    if git(project, "rev-parse", "HEAD").lower() != commit_sha:
        raise RequestError("工作樹 HEAD 與候選提交不一致")
    if git(project, "rev-parse", "HEAD^").lower() != assignment.base.head_sha:
        raise RequestError("候選測試提交的父提交不是已驗證的 base SHA")
    require_only_path(
        git_nul_paths(
            project,
            "diff-tree",
            "--no-commit-id",
            "--name-only",
            "-r",
            "-z",
            "HEAD",
        ),
        assignment.test_file,
        "候選測試提交",
    )
    if (
        committed_file_digest(project, commit_sha, assignment.test_file)
        != expected_digest
    ):
        raise RequestError("候選測試提交內容與驗證憑證不一致")
    if changed_paths(project):
        raise RequestError("候選測試提交後仍有未提交變更")


def commit_candidate(assignment: Assignment, receipt: dict[str, Any]) -> str:
    project = assignment.worktree
    digest = receipt["candidate_sha256"]
    snapshot = candidate_snapshot(assignment, {}, require_cases=False)
    if snapshot["sha256"] != digest:
        raise RequestError("候選測試在驗證通過後又被修改，請重新驗證")
    git(project, "add", "--", assignment.test_file, message="無法暫存候選測試")
    require_only_path(
        git_nul_paths(project, "diff", "--cached", "--name-only", "-z", "--"),
        assignment.test_file,
        "建立提交前",
    )
    if git_nul_paths(project, "diff", "--name-only", "-z", "--") or git_nul_paths(
        project, "ls-files", "--others", "--exclude-standard", "-z"
    ):
        raise RequestError("建立提交前仍有未暫存或未追蹤的額外變更")
    git(
        project,
        "diff",
        "--cached",
        "--check",
        "--",
        message="候選測試未通過 git diff --check",
    )
    git(
        project,
        "-c",
        "commit.gpgSign=false",
        "commit",
        "--quiet",
        "-m",
        f"新增 {assignment.target_class} 單元測試",
        "-m",
        f"Candidate-SHA256: {digest}",
        message="無法建立候選測試提交",
    )
    commit_sha = git(project, "rev-parse", "HEAD").lower()
    verify_candidate_commit(assignment, commit_sha, digest)
    return commit_sha


def push_branch(assignment: Assignment, commit_sha: str) -> str:
    project = assignment.worktree
    result = run_command(
        [
            "git",
            "-C",
            str(project),
            "push",
            "--porcelain",
            assignment.base.remote,
            f"HEAD:refs/heads/{assignment.branch}",
        ],
        cwd=project,
        timeout=GIT_TIMEOUT_SECONDS,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
    if result.returncode != 0:
        raise command_failure(result, f"無法推送分支 {assignment.branch}")
    live_sha = remote_sha(project, assignment.base.remote, assignment.branch)
    if live_sha != commit_sha:
        raise RequestError(
            f"推送後遠端 SHA 不一致：預期 {commit_sha}，實際 {live_sha or '(不存在)'}"
        )
    return live_sha


def pr_body(
    assignment: Assignment,
    receipt: dict[str, Any],
    commit_sha: str,
) -> str:
    validation = receipt["result"]
    cases = [
        (
            f"### {case['id']}\n\n"
            f"- 情境：{case['scenario']}\n"
            f"- 預期：{case['expected']}\n"
            f"- 規格依據：{case['evidence']}"
        )
        for case in receipt["test_cases"]
    ]
    body = (
        "## 單元測試候選\n\n"
        f"- 受測類別：`{assignment.target_class}`\n"
        f"- 測試檔：`{assignment.test_file}`\n"
        f"- 基準：`{assignment.base.remote_branch}` (`{assignment.base.head_sha}`)\n"
        f"- 分支：`{assignment.branch}`\n"
        f"- 提交：`{commit_sha}`\n"
        f"- 候選內容 SHA-256：`{receipt['candidate_sha256']}`\n\n"
        "## 本機驗證\n\n"
        f"- 指令：`{validation['command']}`\n"
        f"- 實際執行測試：{validation['candidate_tests']['executed']}\n"
        f"- 目標類別行覆蓋率：{validation['coverage']['percent']:.2f}%"
        f"（門檻 {validation['coverage']['minimum_percent']}%）\n\n"
        "## 測試案例與依據\n\n"
        + "\n\n".join(cases)
        + "\n\n---\n\n此 PR 必須由工程師審查；工具不會轉為 Ready，也不會合併。\n"
    )
    if len(body.encode("utf-8")) > MAX_PR_BODY_BYTES:
        raise RequestError(f"Draft PR 內容超過 {MAX_PR_BODY_BYTES} bytes")
    return body


def verify_pr(
    assignment: Assignment,
    details: dict[str, Any],
    commit_sha: str,
) -> dict[str, Any]:
    expected = {
        "isDraft": True,
        "state": "OPEN",
        "headRefName": assignment.branch,
        "headRefOid": commit_sha,
        "baseRefName": assignment.base.remote_branch,
    }
    mismatches = [key for key, value in expected.items() if details.get(key) != value]
    if mismatches:
        raise RequestError("Draft PR 驗證失敗：" + ", ".join(mismatches))
    if not isinstance(details.get("number"), int) or not isinstance(
        details.get("url"), str
    ):
        raise RequestError("Draft PR 缺少編號或 URL")
    return details


def create_draft_pr(
    assignment: Assignment,
    receipt: dict[str, Any],
    commit_sha: str,
) -> dict[str, Any]:
    body = pr_body(assignment, receipt, commit_sha)
    with tempfile.TemporaryDirectory(prefix="opencode-unit-test-pr-") as temporary:
        body_file = Path(temporary) / "body.md"
        body_file.write_text(body, encoding="utf-8")
        created = run_command(
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
                "--body-file",
                str(body_file),
            ],
            cwd=assignment.worktree,
            timeout=GITHUB_TIMEOUT_SECONDS,
            env=github_environment(),
        )
    if created.returncode != 0:
        raise command_failure(created, "無法建立 Draft PR")
    urls = re.findall(r"https?://[^\s]+", created.stdout)
    if not urls:
        raise RequestError("gh pr create 沒有回傳 PR URL")
    url = urls[-1].rstrip(".,)")
    viewed = run_command(
        [
            "gh",
            "pr",
            "view",
            url,
            "--repo",
            github_locator(assignment),
            "--json",
            "number,url,isDraft,state,headRefName,headRefOid,baseRefName",
        ],
        cwd=assignment.worktree,
        timeout=GITHUB_TIMEOUT_SECONDS,
        env=github_environment(),
    )
    if viewed.returncode != 0:
        raise command_failure(viewed, "無法驗證新建 Draft PR")
    try:
        details = json.loads(viewed.stdout)
    except json.JSONDecodeError as exc:
        raise RequestError("gh pr view 沒有回傳有效 JSON") from exc
    if not isinstance(details, dict):
        raise RequestError("gh pr view 回傳格式無效")
    return verify_pr(assignment, details, commit_sha)


def published_result(
    assignment: Assignment,
    receipt: dict[str, Any],
    commit_sha: str,
    live_sha: str,
    pr: dict[str, Any],
) -> dict[str, Any]:
    return {
        "status": "draft-pr-created",
        "message": "候選測試已提交、推送，並建立等待人工審查的 Draft PR。",
        "assignment_id": assignment.assignment_id,
        "validation_id": receipt["validation_id"],
        "target_class": assignment.target_class,
        "test_file": assignment.test_file,
        "worktree": str(assignment.worktree.relative_to(assignment.coordinator_repo)),
        "worktree_retained": True,
        "base_branch": assignment.base.remote_branch,
        "base_sha": assignment.base.head_sha,
        "branch": assignment.branch,
        "commit_sha": commit_sha,
        "remote_sha": live_sha,
        "submitted": True,
        "pr_created": True,
        "pr_verified": True,
        "merged": False,
        "pr": {"number": pr["number"], "url": pr["url"], "draft": pr["isDraft"]},
        "validation": receipt["result"],
    }


def publish(assignment: Assignment, validation_id: str) -> dict[str, Any]:
    receipt = validation_receipt(assignment, validation_id)
    require_remote_sha(
        assignment.worktree,
        assignment.base.remote,
        assignment.base.remote_branch,
        assignment.base.head_sha,
        "發布前",
    )
    if (
        remote_sha(assignment.worktree, assignment.base.remote, assignment.branch)
        is not None
    ):
        raise RequestError(
            f"遠端派工分支已存在：{assignment.base.remote}/{assignment.branch}"
        )
    commit_sha = commit_candidate(assignment, receipt)
    require_remote_sha(
        assignment.worktree,
        assignment.base.remote,
        assignment.base.remote_branch,
        assignment.base.head_sha,
        "推送前",
    )
    live_sha = push_branch(assignment, commit_sha)
    require_remote_sha(
        assignment.worktree,
        assignment.base.remote,
        assignment.base.remote_branch,
        assignment.base.head_sha,
        "建立 PR 前",
    )
    pr = create_draft_pr(assignment, receipt, commit_sha)
    require_remote_sha(
        assignment.worktree,
        assignment.base.remote,
        assignment.base.remote_branch,
        assignment.base.head_sha,
        "PR 建立後",
    )
    result = published_result(assignment, receipt, commit_sha, live_sha, pr)
    state = dict(assignment.state)
    state["status"] = "published"
    state["publication"] = {
        "commit_sha": commit_sha,
        "remote_sha": live_sha,
        "pr": result["pr"],
    }
    save_assignment_state(assignment, state)
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
        assignment_id = parse_required_string(data, "assignment_id")
        validation_id = parse_required_string(data, "validation_id")
        assignment = load_assignment(
            repo,
            assignment_id,
            session_id,
            bind_worker=False,
        )
        result = publish(assignment, validation_id)
        successful = result["status"] == "draft-pr-created"
    except (RequestError, OSError, UnicodeError) as exc:
        result = {
            "status": "cancelled" if cancelled() else "publication-failed",
            "message": str(exc),
            "submitted": False,
            "pr_created": False,
            "merged": False,
        }
        if assignment is not None:
            result.update(
                {
                    "assignment_id": assignment.assignment_id,
                    "target_class": assignment.target_class,
                    "test_file": assignment.test_file,
                    "worktree": str(
                        assignment.worktree.relative_to(assignment.coordinator_repo)
                    ),
                    "worktree_retained": True,
                    "branch": assignment.branch,
                    "base_sha": assignment.base.head_sha,
                }
            )
        successful = False
    except Exception as exc:  # noqa: BLE001 - CLI 邊界需回傳結構化錯誤
        result = {
            "status": "internal-error",
            "message": str(exc),
            "submitted": False,
            "pr_created": False,
            "merged": False,
        }
        successful = False
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if successful else 3


if __name__ == "__main__":
    raise SystemExit(main())
