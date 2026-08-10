"""發布最新驗證通過的候選測試：提交、推送並建立 Draft PR。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
import urllib.parse
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


class RemoteStateUnknownError(RequestError):
    """遠端操作已發出，但無法可靠判斷最後狀態。"""


def github_locator(assignment: Assignment) -> str:
    base = assignment.base
    if base.github_host == "github.com":
        return base.github_repository
    return f"{base.github_host}/{base.github_repository}"


def compare_url(assignment: Assignment) -> str:
    base = urllib.parse.quote(assignment.base.remote_branch, safe="")
    head = urllib.parse.quote(assignment.branch, safe="")
    return (
        f"https://{assignment.base.github_host}/{assignment.base.github_repository}/"
        f"compare/{base}...{head}?expand=1"
    )


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
        raise RequestError("工作樹 HEAD 與發布狀態中的提交不一致")
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


def create_or_reuse_commit(
    assignment: Assignment,
    receipt: dict[str, Any],
    publication: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    project = assignment.worktree
    digest = receipt["candidate_sha256"]
    head = git(project, "rev-parse", "HEAD").lower()
    recorded = publication.get("commit_sha")
    if head != assignment.base.head_sha:
        if recorded is not None and recorded != head:
            raise RequestError("派工分支已有無法對帳的提交，需要人工確認")
        verify_candidate_commit(assignment, head, digest)
        publication = {**publication, "stage": "committed", "commit_sha": head}
        state = dict(assignment.state)
        state["status"] = "publishing"
        state["publication"] = publication
        save_assignment_state(assignment, state)
        assignment.state.update(state)
        return head, publication

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
    title = f"新增 {assignment.target_class} 單元測試"
    git(
        project,
        "-c",
        "commit.gpgSign=false",
        "commit",
        "--quiet",
        "-m",
        title,
        "-m",
        f"Candidate-SHA256: {digest}",
        message="無法建立候選測試提交",
    )
    commit_sha = git(project, "rev-parse", "HEAD").lower()
    verify_candidate_commit(assignment, commit_sha, digest)
    publication = {**publication, "stage": "committed", "commit_sha": commit_sha}
    state = dict(assignment.state)
    state["status"] = "publishing"
    state["publication"] = publication
    save_assignment_state(assignment, state)
    assignment.state.update(state)
    return commit_sha, publication


def push_or_reconcile(
    assignment: Assignment,
    commit_sha: str,
    publication: dict[str, Any],
) -> dict[str, Any]:
    project = assignment.worktree
    observed = remote_sha(project, assignment.base.remote, assignment.branch)
    if observed is not None and observed != commit_sha:
        raise RequestError(
            f"遠端派工分支指向其他提交：預期 {commit_sha}，實際 {observed}"
        )
    if observed is None:
        pushed = run_command(
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
        if pushed.returncode != 0:
            try:
                observed = remote_sha(
                    project,
                    assignment.base.remote,
                    assignment.branch,
                    allow_cancelled=True,
                )
            except RequestError as exc:
                raise RemoteStateUnknownError(
                    "推送沒有成功回覆，且無法重新查詢遠端分支；請人工確認後再重跑。"
                ) from exc
            if observed != commit_sha:
                raise command_failure(pushed, f"無法推送分支 {assignment.branch}")
    live_sha = remote_sha(
        project,
        assignment.base.remote,
        assignment.branch,
        allow_cancelled=True,
    )
    if live_sha != commit_sha:
        raise RemoteStateUnknownError(
            f"推送後遠端 SHA 無法確認：預期 {commit_sha}，實際 {live_sha or '(不存在)'}"
        )
    publication = {
        **publication,
        "stage": "pushed",
        "commit_sha": commit_sha,
        "remote_sha": live_sha,
    }
    state = dict(assignment.state)
    state["status"] = "publishing"
    state["publication"] = publication
    save_assignment_state(assignment, state)
    assignment.state.update(state)
    return publication


def pr_body(
    assignment: Assignment,
    receipt: dict[str, Any],
    commit_sha: str,
) -> str:
    validation = receipt["result"]
    case_sections = [
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
        + "\n\n".join(case_sections)
        + "\n\n---\n\n此 PR 必須由工程師審查；工具不會轉為 Ready，也不會合併。\n"
    )
    if len(body.encode("utf-8")) > MAX_PR_BODY_BYTES:
        raise RequestError(f"Draft PR 內容超過 {MAX_PR_BODY_BYTES} bytes")
    return body


def list_existing_prs(assignment: Assignment) -> list[dict[str, Any]]:
    result = run_command(
        [
            "gh",
            "pr",
            "list",
            "--repo",
            github_locator(assignment),
            "--state",
            "all",
            "--head",
            assignment.branch,
            "--base",
            assignment.base.remote_branch,
            "--limit",
            "10",
            "--json",
            "number,url,isDraft,state,headRefName,headRefOid,baseRefName",
        ],
        cwd=assignment.worktree,
        timeout=GITHUB_TIMEOUT_SECONDS,
        env=github_environment(),
    )
    if result.returncode != 0:
        raise command_failure(result, "無法查詢既有 PR")
    try:
        values = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RequestError("gh pr list 沒有回傳有效 JSON") from exc
    if not isinstance(values, list) or any(
        not isinstance(item, dict) for item in values
    ):
        raise RequestError("gh pr list 回傳格式無效")
    return values


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


def create_pr(
    assignment: Assignment,
    receipt: dict[str, Any],
    commit_sha: str,
) -> None:
    body = pr_body(assignment, receipt, commit_sha)
    with tempfile.TemporaryDirectory(prefix="opencode-unit-test-pr-") as temporary:
        body_file = Path(temporary) / "body.md"
        body_file.write_text(body, encoding="utf-8")
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
                "--body-file",
                str(body_file),
            ],
            cwd=assignment.worktree,
            timeout=GITHUB_TIMEOUT_SECONDS,
            env=github_environment(),
        )
    if result.returncode != 0:
        raise command_failure(result, "gh pr create 沒有成功回覆")


def create_or_reconcile_pr(
    assignment: Assignment,
    receipt: dict[str, Any],
    commit_sha: str,
) -> dict[str, Any]:
    existing = list_existing_prs(assignment)
    if len(existing) > 1:
        raise RequestError("同一派工分支存在多個 PR，需要人工確認")
    if not existing:
        try:
            create_pr(assignment, receipt, commit_sha)
        except RequestError:
            try:
                existing = list_existing_prs(assignment)
            except RequestError as query_error:
                raise RemoteStateUnknownError(
                    "建立 PR 沒有成功回覆，且無法重新查詢；請人工確認後再重跑。"
                ) from query_error
            if not existing:
                raise
        else:
            try:
                existing = list_existing_prs(assignment)
            except RequestError as query_error:
                raise RemoteStateUnknownError(
                    "建立 PR 已成功回覆，但無法重新查詢驗證；請人工確認後再重跑。"
                ) from query_error
    if len(existing) != 1:
        raise RemoteStateUnknownError("建立 PR 後沒有取得唯一可驗證結果")
    return verify_pr(assignment, existing[0], commit_sha)


def verify_published(
    assignment: Assignment,
    receipt: dict[str, Any],
    publication: dict[str, Any],
) -> dict[str, Any]:
    commit_sha = publication.get("commit_sha")
    pr = publication.get("pr")
    if not isinstance(commit_sha, str) or not isinstance(pr, dict):
        raise RequestError("已發布狀態不完整")
    verify_candidate_commit(assignment, commit_sha, receipt["candidate_sha256"])
    live_sha = remote_sha(
        assignment.worktree, assignment.base.remote, assignment.branch
    )
    if live_sha != commit_sha:
        raise RequestError("已發布分支的遠端 SHA 已改變")
    matching = [
        item
        for item in list_existing_prs(assignment)
        if item.get("url") == pr.get("url")
    ]
    if len(matching) != 1:
        raise RequestError("找不到先前已驗證的 Draft PR")
    details = verify_pr(assignment, matching[0], commit_sha)
    return published_result(
        assignment, receipt, commit_sha, live_sha, details, reused=True
    )


def published_result(
    assignment: Assignment,
    receipt: dict[str, Any],
    commit_sha: str,
    live_sha: str,
    pr: dict[str, Any],
    *,
    reused: bool,
) -> dict[str, Any]:
    return {
        "status": "draft-pr-created",
        "message": (
            "已重新核對既有提交、遠端分支與 Draft PR。"
            if reused
            else "候選測試已提交、推送，並建立等待人工審查的 Draft PR。"
        ),
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
    publication = assignment.state.get("publication")
    if not isinstance(publication, dict):
        publication = {}
    if assignment.state.get("status") == "published":
        return verify_published(assignment, receipt, publication)

    require_remote_sha(
        assignment.worktree,
        assignment.base.remote,
        assignment.base.remote_branch,
        assignment.base.head_sha,
        "發布前",
    )
    commit_sha, publication = create_or_reuse_commit(assignment, receipt, publication)
    publication = push_or_reconcile(assignment, commit_sha, publication)
    require_remote_sha(
        assignment.worktree,
        assignment.base.remote,
        assignment.base.remote_branch,
        assignment.base.head_sha,
        "建立 PR 前",
    )
    pr = create_or_reconcile_pr(assignment, receipt, commit_sha)
    live_sha = remote_sha(
        assignment.worktree, assignment.base.remote, assignment.branch
    )
    if live_sha != commit_sha:
        raise RequestError("PR 建立後遠端分支 SHA 與候選提交不一致")
    require_remote_sha(
        assignment.worktree,
        assignment.base.remote,
        assignment.base.remote_branch,
        assignment.base.head_sha,
        "PR 建立後",
    )
    result = published_result(
        assignment, receipt, commit_sha, live_sha, pr, reused=False
    )
    state = dict(assignment.state)
    state["status"] = "published"
    state["publication"] = {
        **publication,
        "stage": "published",
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
            require_base_head=False,
        )
        result = publish(assignment, validation_id)
        successful = result["status"] == "draft-pr-created"
    except (RequestError, OSError, UnicodeError) as exc:
        unknown = isinstance(exc, RemoteStateUnknownError)
        publication = assignment.state.get("publication") if assignment else None
        submitted: bool | None = False
        if isinstance(publication, dict) and publication.get("remote_sha"):
            submitted = True
        elif unknown:
            submitted = None
        result = {
            "status": "cancelled"
            if cancelled()
            else "remote-state-unknown"
            if unknown
            else "publication-failed",
            "message": str(exc),
            "submitted": submitted,
            "pr_created": None if unknown else False,
            "merged": False,
            "manual_recovery_required": unknown,
            "automatic_retry_supported": True,
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
                    "compare_url": compare_url(assignment),
                }
            )
        successful = False
    except Exception as exc:  # noqa: BLE001 - CLI 邊界需回傳結構化錯誤
        result = {
            "status": "internal-error",
            "message": str(exc),
            "submitted": None,
            "pr_created": None,
            "merged": False,
        }
        successful = False
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if successful else 3


if __name__ == "__main__":
    raise SystemExit(main())
