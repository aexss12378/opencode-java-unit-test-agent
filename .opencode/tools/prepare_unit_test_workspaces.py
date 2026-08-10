"""盤點所有具體 Service，並為每個 Service 建立可見的獨立工作樹。"""

from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path
from typing import Any

from _unit_test_common import (
    ASSIGNMENT_VERSION,
    BATCH_EXECUTION_MODE,
    MAX_TARGETS,
    BaseContext,
    BranchConflictError,
    RequestError,
    assignment_digest,
    assignment_state_path,
    atomic_write_json,
    base_context,
    base_to_json,
    branch_name,
    cancelled,
    command_failure,
    install_signal_handlers,
    parse_required_string,
    read_input,
    remote_sha,
    repo_root,
    require_remote_sha,
    run_command,
    validate_session_id,
    validate_target,
)

GIT_TIMEOUT_SECONDS = 120
PACKAGE = re.compile(
    r"^\s*package\s+([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)\s*;",
    re.MULTILINE,
)
JAVA_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
JAVA_LINE_COMMENT = re.compile(r"//[^\r\n]*")


def discover_concrete_services(repo: Path) -> list[str]:
    source_root = repo / "src" / "main" / "java"
    if not source_root.is_dir():
        raise RequestError("專案缺少 src/main/java，無法盤點 Service")
    discovered: list[str] = []
    for source in sorted(source_root.rglob("*Service.java")):
        if source.is_symlink() or not source.is_file():
            continue
        try:
            content = source.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise RequestError(
                f"無法讀取 Service 原始碼：{source.relative_to(repo)}"
            ) from exc
        package_match = PACKAGE.search(content)
        if package_match is None:
            raise RequestError(
                f"Service 原始碼缺少 package：{source.relative_to(repo)}"
            )
        simple_name = source.stem
        code = JAVA_LINE_COMMENT.sub("", JAVA_BLOCK_COMMENT.sub("", content))
        declaration = re.search(
            rf"(?m)(?:^|;)\s*(?:(?:@[A-Za-z_$][\w$]*(?:\([^)]*\))?)\s*)*"
            rf"(?P<modifiers>(?:(?:public|abstract|final|sealed|non-sealed|strictfp)\s+)*)"
            rf"class\s+{re.escape(simple_name)}\b",
            code,
        )
        if declaration is None or "abstract" in declaration.group("modifiers").split():
            continue
        discovered.append(f"{package_match.group(1)}.{simple_name}")
    return sorted(discovered)


def normalize_specification_source(repo: Path, target_class: str, value: str) -> str:
    source = value.strip()
    candidate = Path(source)
    if candidate.is_absolute():
        raise RequestError(f"{target_class} 的外部規格必須使用專案相對路徑：{source}")
    source_path = repo / candidate
    resolved = source_path.resolve()
    try:
        relative = resolved.relative_to(repo.resolve())
    except ValueError as exc:
        raise RequestError(f"{target_class} 的外部規格離開專案範圍：{source}") from exc
    if source_path.is_symlink() or not resolved.is_file():
        raise RequestError(f"{target_class} 的外部規格不存在或不是一般檔案：{source}")
    relative_text = relative.as_posix()
    allowed_document = (
        (len(relative.parts) == 1 and relative.name.lower().startswith("readme"))
        or relative.parts[:1] == ("docs",)
        or relative.parts[:3] == ("src", "main", "resources")
    )
    if not allowed_document:
        raise RequestError(
            f"{target_class} 的外部規格只允許 README、docs 或 src/main/resources"
        )
    return relative_text


def validate_request(repo: Path, data: dict[str, Any]) -> dict[str, Any]:
    if data.get("execution_mode") != BATCH_EXECUTION_MODE:
        raise RequestError(f"execution_mode 必須是 {BATCH_EXECUTION_MODE}")
    raw_targets = data.get("targets")
    if not isinstance(raw_targets, list) or len(raw_targets) > MAX_TARGETS:
        raise RequestError(f"targets 必須是最多 {MAX_TARGETS} 項的陣列")

    seen: set[str] = set()
    targets: list[dict[str, Any]] = []
    for raw in raw_targets:
        if not isinstance(raw, dict):
            raise RequestError("每個派工目標都必須是物件")
        target_class = parse_required_string(raw, "target_class")
        if target_class in seen:
            raise RequestError(f"Service 不得重複：{target_class}")
        seen.add(target_class)
        target = validate_target(repo, target_class)
        raw_sources = raw.get("specification_sources", [])
        if not isinstance(raw_sources, list) or len(raw_sources) > 20:
            raise RequestError(f"{target_class} 的外部規格必須是最多 20 個檔案路徑")
        sources: list[str] = []
        for value in raw_sources:
            if not isinstance(value, str) or not value.strip() or len(value) > 500:
                raise RequestError(f"{target_class} 的外部規格路徑格式無效")
            sources.append(normalize_specification_source(repo, target_class, value))
        targets.append({**target, "specification_sources": sources})

    discovered = discover_concrete_services(repo)
    requested = sorted(seen)
    if requested != discovered:
        missing = sorted(set(discovered) - set(requested))
        unexpected = sorted(set(requested) - set(discovered))
        details: list[str] = []
        if missing:
            details.append("缺少：" + ", ".join(missing))
        if unexpected:
            details.append("不在固定範圍：" + ", ".join(unexpected))
        raise RequestError("全部 Service 的派工清單不完整；" + "；".join(details))
    return {
        "execution_mode": BATCH_EXECUTION_MODE,
        "targets": sorted(targets, key=lambda item: item["target_class"]),
        "target_order": discovered,
    }


def ensure_branch_available(repo: Path, base: BaseContext, branch: str) -> None:
    local = run_command(
        [
            "git",
            "-C",
            str(repo),
            "show-ref",
            "--verify",
            "--quiet",
            f"refs/heads/{branch}",
        ],
        cwd=repo,
        timeout=GIT_TIMEOUT_SECONDS,
    )
    if local.returncode == 0:
        raise BranchConflictError(f"本機分支已存在，需要人工確認：{branch}")
    if local.returncode != 1:
        raise command_failure(local, f"無法檢查本機分支 {branch}")
    if remote_sha(repo, base.remote, branch) is not None:
        raise BranchConflictError(
            f"遠端分支已存在，需要人工確認：{base.remote}/{branch}"
        )


def rollback_incomplete_worktree(repo: Path, worktree: Path, branch: str) -> None:
    if worktree.exists():
        run_command(
            ["git", "-C", str(repo), "worktree", "remove", "--force", str(worktree)],
            cwd=repo,
            timeout=GIT_TIMEOUT_SECONDS,
            allow_cancelled=True,
        )
    if worktree.exists():
        shutil.rmtree(worktree, ignore_errors=True)
    run_command(
        ["git", "-C", str(repo), "branch", "-D", "--", branch],
        cwd=repo,
        timeout=GIT_TIMEOUT_SECONDS,
        allow_cancelled=True,
    )


def worker_prompt(repo: Path, worktree: Path, state: dict[str, Any]) -> str:
    relative_worktree = worktree.relative_to(repo).as_posix()
    source_path = f"{relative_worktree}/{state['target_source']}"
    test_path = f"{relative_worktree}/{state['test_file']}"
    sources = state["specification_sources"]
    external_specs = (
        "\n".join(f"- {source}" for source in sources) if sources else "- 無"
    )
    return (
        f"execution_mode: {BATCH_EXECUTION_MODE}\n"
        f"assignment_id: {state['assignment_id']}\n"
        f"target_class: {state['target_class']}\n"
        f"worktree_path: {relative_worktree}\n"
        f"target_source_path: {source_path}\n"
        f"test_file_path: {test_path}\n"
        "外部規格檔案：\n"
        f"{external_specs}\n\n"
        "有外部規格時以規格為準；沒有時依目前可觀察行為建立測試，"
        "並在 evidence 標示目前實作依據。\n\n"
        "只處理這一個 Service。先獨立整理案例的 scenario、expected、evidence，再用 edit 修改唯一的 "
        f"test_file_path。完成後呼叫 validate_unit_test；通過後立即呼叫 publish_unit_test。"
        "不得修改正式原始碼、pom.xml 或其他測試檔。"
    )


def prepare_assignment(
    repo: Path,
    base: BaseContext,
    coordinator_session_id: str,
    target: dict[str, Any],
) -> dict[str, Any]:
    assignment_id = assignment_digest(
        coordinator_session_id, target["target_class"], base.head_sha
    )[:24]
    branch = branch_name(coordinator_session_id, target["target_class"], base.head_sha)
    slug = re.sub(
        r"[^a-z0-9]+", "-", target["target_class"].rsplit(".", 1)[-1].lower()
    ).strip("-")
    worktree = repo / "unit-test-worktrees" / f"{slug}-{assignment_id[:8]}"
    state_path = assignment_state_path(repo, assignment_id)
    if state_path.exists() or worktree.exists():
        raise BranchConflictError(
            f"派工狀態或工作樹已存在，需要人工確認：{assignment_id}"
        )
    ensure_branch_available(repo, base, branch)
    worktree.parent.mkdir(parents=True, exist_ok=True)
    added = run_command(
        [
            "git",
            "-C",
            str(repo),
            "worktree",
            "add",
            "--quiet",
            "-b",
            branch,
            str(worktree),
            base.head_sha,
        ],
        cwd=repo,
        timeout=GIT_TIMEOUT_SECONDS,
    )
    if added.returncode != 0:
        rollback_incomplete_worktree(repo, worktree, branch)
        raise command_failure(added, f"無法建立測試工作樹 {branch}")
    try:
        state = {
            "version": ASSIGNMENT_VERSION,
            "status": "prepared",
            "assignment_id": assignment_id,
            "coordinator_session_id": coordinator_session_id,
            "worker_session_id": None,
            "coordinator_repo": str(repo),
            "worktree": str(worktree),
            "branch": branch,
            "target_class": target["target_class"],
            "target_source": target["target_source"],
            "candidate_class": target["candidate_class"],
            "test_file": target["test_file"],
            "specification_sources": target["specification_sources"],
            "base": base_to_json(base),
            "validation": None,
            "publication": None,
        }
        atomic_write_json(state_path, state)
    except Exception:
        state_path.unlink(missing_ok=True)
        rollback_incomplete_worktree(repo, worktree, branch)
        raise
    relative_worktree = worktree.relative_to(repo).as_posix()
    return {
        "assignment_id": assignment_id,
        "target_class": target["target_class"],
        "target_source_path": f"{relative_worktree}/{target['target_source']}",
        "test_file_path": f"{relative_worktree}/{target['test_file']}",
        "branch": branch,
        "base_sha": base.head_sha,
        "worktree": relative_worktree,
        "prompt": worker_prompt(repo, worktree, state),
    }


def prepare(repo: Path, session_id: str, request: dict[str, Any]) -> dict[str, Any]:
    base = base_context(repo)
    ignored = run_command(
        ["git", "-C", str(repo), "check-ignore", "-q", "unit-test-worktrees/example"],
        cwd=repo,
        timeout=GIT_TIMEOUT_SECONDS,
    )
    if ignored.returncode != 0:
        raise RequestError(".gitignore 必須排除 unit-test-worktrees/")
    prepared: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for target in request["targets"]:
        try:
            require_remote_sha(
                repo, base.remote, base.remote_branch, base.head_sha, "建立工作樹前"
            )
            prepared.append(prepare_assignment(repo, base, session_id, target))
        except (RequestError, OSError, UnicodeError) as exc:
            failures.append(
                {
                    "status": "branch-conflict"
                    if isinstance(exc, BranchConflictError)
                    else "preparation-failed",
                    "message": str(exc),
                    "target_class": target["target_class"],
                    "test_file": target["test_file"],
                    "base_sha": base.head_sha,
                    "submitted": False,
                    "pr_created": False,
                    "merged": False,
                }
            )
    status = (
        "prepared"
        if len(prepared) == len(request["targets"])
        else "partially-prepared"
        if prepared
        else "preparation-failed"
    )
    return {
        "status": status,
        "message": (
            f"{len(prepared)} 個 Service 工作樹已準備；{len(failures)} 個建立失敗。"
        ),
        "execution_mode": request["execution_mode"],
        "base_branch": base.remote_branch,
        "base_sha": base.head_sha,
        "service_count": len(request["target_order"]),
        "target_order": request["target_order"],
        "prepared": prepared,
        "results": failures,
    }


def main() -> int:
    install_signal_handlers()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".")
    parser.add_argument("--session-id", required=True)
    args = parser.parse_args()
    try:
        repo = repo_root(args.repo)
        session_id = validate_session_id(args.session_id)
        request = validate_request(repo, read_input())
        result = prepare(repo, session_id, request)
        successful = result["status"] == "prepared"
    except (RequestError, OSError, UnicodeError) as exc:
        result = {
            "status": "cancelled" if cancelled() else "invalid-request",
            "message": str(exc),
            "submitted": False,
            "pr_created": False,
            "merged": False,
        }
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
