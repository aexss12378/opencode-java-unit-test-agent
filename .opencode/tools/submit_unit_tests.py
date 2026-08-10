"""以獨立 Git worktree 派發、驗證並發布 Java 單元測試工作。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

MAX_FILE_BYTES = 100_000
MAX_PR_BODY_BYTES = 60_000
MAX_TARGETS = 50
MAX_CONCURRENCY = 8
MAVEN_TIMEOUT_SECONDS = 600
GIT_TIMEOUT_SECONDS = 120
GITHUB_TIMEOUT_SECONDS = 120
MINIMUM_LINE_COVERAGE_PERCENT = 80
BRANCH_PREFIX = "opencode/unit-test"
TRUSTED_BASE_BRANCH = "main"
ASSIGNMENT_VERSION = 1
BATCH_EXECUTION_MODE = "unit-test-all/v1"
CONFIRMED_EXECUTION_MODE = "confirmed-targets"
NOT_STARTED_REASONS = {"缺少可信規格證據", "可信規格彼此衝突"}
CASE_ID = re.compile(r"^UT-[0-9]{3,}$")
SESSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,200}$")
JAVA_CLASS = re.compile(r"^[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)+$")
PACKAGE = re.compile(
    r"^\s*package\s+([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)\s*;",
    re.MULTILINE,
)
JAVA_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
JAVA_LINE_COMMENT = re.compile(r"//[^\r\n]*")
PUBLIC_JAVADOC = re.compile(
    r"/\*\*.*?\*/\s*(?:(?:@[A-Za-z_$][\w$]*(?:\([^)]*\))?)\s*)*(?:public|protected)\s+",
    re.DOTALL,
)
_ACTIVE_PROCESSES: set[subprocess.Popen[str]] = set()
_ACTIVE_PROCESSES_LOCK = threading.RLock()
_CANCEL_REQUESTED = False


class RequestError(RuntimeError):
    pass


class DraftPrVerificationError(RequestError):
    def __init__(self, message: str, pr_url: str) -> None:
        super().__init__(message)
        self.pr_url = pr_url


class DraftPrStateUnknownError(RequestError):
    pass


class BranchConflictError(RequestError):
    pass


@dataclass(frozen=True)
class BaseContext:
    branch: str
    head_sha: str
    remote: str
    remote_branch: str
    github_host: str
    github_repository: str


@dataclass(frozen=True)
class Worktree:
    root: Path
    project: Path
    branch: str


@dataclass(frozen=True)
class Assignment:
    assignment_id: str
    coordinator_session_id: str
    worker_session_id: str | None
    coordinator_repo: Path
    common_git_dir: Path
    manifest_path: Path
    result_path: Path
    branch: str
    target_class: str
    target_source: str
    candidate_class: str
    test_file: str
    specification_sources: tuple[str, ...]
    base: BaseContext


# COMMAND


def run_command(
    command: list[str],
    *,
    cwd: Path,
    timeout: int,
    env: dict[str, str] | None = None,
    allow_cancelled: bool = False,
) -> subprocess.CompletedProcess[str]:
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
            env=env,
            start_new_session=True,
        )
    except FileNotFoundError as exc:
        raise RequestError(f"找不到必要指令：{command[0]}") from exc
    with _ACTIVE_PROCESSES_LOCK:
        _ACTIVE_PROCESSES.add(process)
    try:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
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
        with _ACTIVE_PROCESSES_LOCK:
            _ACTIVE_PROCESSES.discard(process)


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
    with _ACTIVE_PROCESSES_LOCK:
        active = tuple(_ACTIVE_PROCESSES)
    for process in active:
        terminate_process(process)


def install_signal_handlers() -> None:
    signal.signal(signal.SIGTERM, request_cancellation)
    signal.signal(signal.SIGINT, request_cancellation)


def checked_command(
    command: list[str],
    *,
    cwd: Path,
    timeout: int,
    message: str,
) -> str:
    result = run_command(command, cwd=cwd, timeout=timeout)
    if result.returncode != 0:
        detail = (result.stdout + result.stderr).strip()[-4000:]
        raise RequestError(message + (f"：{detail}" if detail else ""))
    return result.stdout.strip()


def git(repo: Path, *arguments: str, message: str = "Git 指令失敗") -> str:
    return checked_command(
        ["git", "-C", str(repo), *arguments],
        cwd=repo,
        timeout=GIT_TIMEOUT_SECONDS,
        message=message,
    )


def optional_git(repo: Path, *arguments: str) -> str | None:
    result = run_command(
        ["git", "-C", str(repo), *arguments],
        cwd=repo,
        timeout=GIT_TIMEOUT_SECONDS,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def command_failure(result: subprocess.CompletedProcess[str], message: str) -> RequestError:
    detail = (result.stdout + result.stderr).strip()[-4000:]
    return RequestError(message + (f"：{detail}" if detail else ""))


# INPUT AND ASSIGNMENT


def repo_root(value: str) -> Path:
    repo = Path(value).resolve()
    if repo != Path.cwd().resolve():
        raise RequestError("--repo 必須指向目前工作目錄")
    if not (repo / "pom.xml").is_file():
        raise RequestError("專案根目錄缺少 pom.xml")
    wrapper = repo / "mvnw"
    if not wrapper.is_file() or not os.access(wrapper, os.X_OK):
        raise RequestError("專案根目錄需要可執行的 mvnw")
    top = git(repo, "rev-parse", "--show-toplevel", message="目前目錄不是 Git worktree")
    if Path(top).resolve() != repo:
        raise RequestError("--repo 必須指向 Git worktree 根目錄")
    return repo


def validate_session_id(value: str) -> str:
    if not SESSION_ID.fullmatch(value):
        raise RequestError("OpenCode session ID 格式無效")
    return value


def read_input() -> dict[str, Any]:
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        raise RequestError(f"輸入不是有效 JSON：{exc}") from exc
    if not isinstance(data, dict):
        raise RequestError("輸入必須是 JSON 物件")
    return data


def destination(root: Path, relative: PurePosixPath) -> Path:
    target = root.joinpath(*relative.parts)
    if not target.resolve(strict=False).is_relative_to(root.resolve()):
        raise RequestError(f"路徑離開專案範圍：{relative}")
    return target


def target_source_path(target_class: str) -> PurePosixPath:
    return PurePosixPath("src", "main", "java", *target_class.split(".")).with_suffix(".java")


def candidate_path(target_class: str) -> PurePosixPath:
    package, _, simple_name = target_class.rpartition(".")
    return PurePosixPath("src", "test", "java", *package.split("."), f"{simple_name}Test.java")


def validate_test_cases(data: dict[str, Any]) -> list[dict[str, str]]:
    raw_cases = data.get("test_cases")
    if not isinstance(raw_cases, list) or not 1 <= len(raw_cases) <= 50:
        raise RequestError("test_cases 數量必須介於 1 到 50")
    cases: list[dict[str, str]] = []
    for raw in raw_cases:
        if not isinstance(raw, dict):
            raise RequestError("每個測試案例都必須是物件")
        case: dict[str, str] = {}
        for key in ("id", "scenario", "expected", "evidence"):
            value = raw.get(key)
            if not isinstance(value, str) or not value.strip():
                raise RequestError(f"測試案例的 {key} 不得為空")
            case[key] = value.strip()
        cases.append(case)
    ids = [case["id"] for case in cases]
    if any(not CASE_ID.fullmatch(case_id) for case_id in ids):
        raise RequestError("測試案例編號必須使用 UT-001 格式")
    if len(ids) != len(set(ids)):
        raise RequestError("測試案例編號不得重複")
    return cases


def validate_target(repo: Path, target_class: str) -> dict[str, str]:
    if not JAVA_CLASS.fullmatch(target_class):
        raise RequestError(f"完整類別名稱格式無效：{target_class}")
    simple_name = target_class.rsplit(".", 1)[-1]
    if not simple_name.endswith("Service"):
        raise RequestError(f"派工目標必須是以 Service 結尾的完整類別名稱：{target_class}")
    source_relative = target_source_path(target_class)
    source = destination(repo, source_relative)
    if source.is_symlink() or not source.is_file():
        raise RequestError(f"找不到正式 Service 原始碼：{target_class}")
    try:
        content = source.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise RequestError(f"無法讀取正式 Service 原始碼：{target_class}") from exc
    expected_package = target_class.rpartition(".")[0]
    package_match = PACKAGE.search(content)
    if package_match is None or package_match.group(1) != expected_package:
        raise RequestError(f"正式 Service 的 package 與完整類別名稱不一致：{target_class}")
    test_relative = candidate_path(target_class)
    candidate_class = f"{target_class}Test"
    return {
        "target_class": target_class,
        "target_source": source_relative.as_posix(),
        "candidate_class": candidate_class,
        "test_file": test_relative.as_posix(),
    }


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
            raise RequestError(f"無法讀取 Service 原始碼：{source.relative_to(repo)}") from exc
        package_match = PACKAGE.search(content)
        if package_match is None:
            raise RequestError(f"Service 原始碼缺少 package：{source.relative_to(repo)}")
        simple_name = source.stem
        code = JAVA_LINE_COMMENT.sub("", JAVA_BLOCK_COMMENT.sub("", content))
        declaration = re.search(
            rf"(?m)(?:^|;)\s*(?:(?:@[A-Za-z_$][\w$]*(?:\([^)]*\))?)\s*)*"
            rf"(?P<modifiers>(?:(?:public|abstract|final|sealed|non-sealed|strictfp)\s+)*)"
            rf"class\s+{re.escape(simple_name)}\b",
            code,
        )
        if declaration is None:
            continue
        modifiers = set(declaration.group("modifiers").split())
        if "abstract" in modifiers:
            continue
        discovered.append(f"{package_match.group(1)}.{simple_name}")
    return sorted(discovered)


def normalize_specification_source(repo: Path, target: dict[str, str], value: str) -> str:
    source = value.strip()
    if source.startswith("使用者需求："):
        requirement = source.removeprefix("使用者需求：").strip()
        if not requirement:
            raise RequestError(f"{target['target_class']} 的使用者需求不得為空")
        return f"使用者需求：{requirement}"

    candidate = Path(source)
    source_path = candidate if candidate.is_absolute() else repo / candidate
    resolved = source_path.resolve()
    try:
        relative = resolved.relative_to(repo.resolve())
    except ValueError as exc:
        raise RequestError(f"{target['target_class']} 的規格來源離開專案範圍：{source}") from exc
    if source_path.is_symlink() or not resolved.is_file():
        raise RequestError(f"{target['target_class']} 的規格來源不存在或不是一般檔案：{source}")
    relative_posix = relative.as_posix()
    allowed_document = (
        (len(relative.parts) == 1 and relative.name.lower().startswith("readme"))
        or relative.parts[:1] == ("docs",)
        or relative.parts[:3] == ("src", "main", "resources")
    )
    if relative_posix == target["target_source"]:
        try:
            content = resolved.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise RequestError(f"無法讀取 {target['target_class']} 的 Javadoc 規格來源") from exc
        if PUBLIC_JAVADOC.search(content) is None:
            raise RequestError(
                f"{target['target_class']} 的正式原始碼沒有公開 Javadoc，不得當成可信規格來源"
            )
    elif not allowed_document:
        raise RequestError(
            f"{target['target_class']} 的規格來源只允許 README、docs、src/main/resources、"
            "目標類別公開 Javadoc或「使用者需求：...」"
        )
    return relative_posix


def validate_dispatch_request(repo: Path, data: dict[str, Any]) -> dict[str, Any]:
    execution_mode = data.get("execution_mode")
    if execution_mode not in {BATCH_EXECUTION_MODE, CONFIRMED_EXECUTION_MODE}:
        raise RequestError(
            f"execution_mode 必須是 {BATCH_EXECUTION_MODE} 或 {CONFIRMED_EXECUTION_MODE}"
        )
    raw_targets = data.get("targets")
    if not isinstance(raw_targets, list) or len(raw_targets) > MAX_TARGETS:
        raise RequestError(f"targets 必須是最多 {MAX_TARGETS} 項的陣列")
    if execution_mode == CONFIRMED_EXECUTION_MODE and not raw_targets:
        raise RequestError("confirmed-targets 模式至少必須提供一個 target")
    max_concurrency = data.get("max_concurrency")
    if isinstance(max_concurrency, bool) or not isinstance(max_concurrency, int):
        raise RequestError("max_concurrency 必須是整數")
    if not 1 <= max_concurrency <= MAX_CONCURRENCY:
        raise RequestError(f"max_concurrency 必須介於 1 到 {MAX_CONCURRENCY}")

    targets: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_targets:
        if not isinstance(raw, dict):
            raise RequestError("每個派工目標都必須是物件")
        target_class = raw.get("target_class")
        if not isinstance(target_class, str) or not target_class.strip():
            raise RequestError("target_class 不得為空")
        target_class = target_class.strip()
        if target_class in seen:
            raise RequestError(f"派工目標不得重複：{target_class}")
        seen.add(target_class)
        raw_sources = raw.get("specification_sources")
        if not isinstance(raw_sources, list) or not 1 <= len(raw_sources) <= 20:
            raise RequestError(f"{target_class} 必須提供 1 到 20 個可信規格來源")
        target = validate_target(repo, target_class)
        sources: list[str] = []
        for value in raw_sources:
            if not isinstance(value, str) or not value.strip():
                raise RequestError(f"{target_class} 的可信規格來源不得為空")
            source = value.strip()
            if len(source) > 4_000:
                raise RequestError(f"{target_class} 的單一可信規格來源不得超過 4000 字元")
            sources.append(normalize_specification_source(repo, target, source))
        targets.append({**target, "specification_sources": sources})
    targets.sort(key=lambda item: item["target_class"])

    raw_not_started = data.get("not_started")
    if not isinstance(raw_not_started, list) or len(raw_not_started) > MAX_TARGETS:
        raise RequestError(f"not_started 必須是最多 {MAX_TARGETS} 項的陣列")
    not_started: list[dict[str, str]] = []
    for raw in raw_not_started:
        if not isinstance(raw, dict):
            raise RequestError("每個 not_started 項目都必須是物件")
        target_class = raw.get("target_class")
        reason = raw.get("reason")
        if not isinstance(target_class, str) or not target_class.strip():
            raise RequestError("not_started.target_class 不得為空")
        target_class = target_class.strip()
        if target_class in seen:
            raise RequestError(f"Service 不得同時出現在 targets 與 not_started：{target_class}")
        if reason not in NOT_STARTED_REASONS:
            raise RequestError(
                f"{target_class} 的 not_started.reason 必須是「缺少可信規格證據」或「可信規格彼此衝突」"
            )
        target = validate_target(repo, target_class)
        seen.add(target_class)
        not_started.append(
            {
                "target_class": target_class,
                "test_file": target["test_file"],
                "reason": reason,
            }
        )
    not_started.sort(key=lambda item: item["target_class"])

    if execution_mode == BATCH_EXECUTION_MODE:
        if max_concurrency != 2:
            raise RequestError("unit-test-all/v1 的 max_concurrency 必須固定為 2")
        discovered = discover_concrete_services(repo)
        classified = sorted(seen)
        if classified != discovered:
            missing = sorted(set(discovered) - set(classified))
            unexpected = sorted(set(classified) - set(discovered))
            detail: list[str] = []
            if missing:
                detail.append("未分類：" + ", ".join(missing))
            if unexpected:
                detail.append("不在固定範圍：" + ", ".join(unexpected))
            raise RequestError("unit-test-all/v1 的 Service 分類不完整；" + "；".join(detail))
        target_order = discovered
    else:
        if not_started:
            raise RequestError("confirmed-targets 模式不得傳入 not_started")
        target_order = [target["target_class"] for target in targets]
    return {
        "execution_mode": execution_mode,
        "targets": targets,
        "not_started": not_started,
        "target_order": target_order,
        "max_concurrency": max_concurrency,
    }


def assignment_digest(session_id: str, target_class: str, base_sha: str) -> str:
    return hashlib.sha256(f"{session_id}\0{target_class}\0{base_sha}".encode("utf-8")).hexdigest()


def branch_name(session_id: str, target_class: str, base_sha: str) -> str:
    simple_name = target_class.rsplit(".", 1)[-1]
    slug = re.sub(r"[^a-z0-9]+", "-", simple_name.lower()).strip("-") or "java-test"
    return f"{BRANCH_PREFIX}/{slug[:48]}-{assignment_digest(session_id, target_class, base_sha)[:12]}"


def base_to_json(base: BaseContext) -> dict[str, str]:
    return {
        "branch": base.branch,
        "head_sha": base.head_sha,
        "remote": base.remote,
        "remote_branch": base.remote_branch,
        "github_host": base.github_host,
        "github_repository": base.github_repository,
    }


def parse_required_string(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise RequestError(f"派工清單缺少有效欄位：{key}")
    return value


def load_assignment(
    repo: Path,
    session_id: str,
    *,
    coordinator: bool = False,
    require_base_head: bool = True,
) -> Assignment:
    manifest_path = repo.resolve().parent / "assignment.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise RequestError("目前 worktree 不是由 dispatch_unit_tests 建立，拒絕驗證或發布")
    if stat.S_IMODE(manifest_path.stat().st_mode) & 0o077:
        raise RequestError("派工清單權限過寬")
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeError) as exc:
        raise RequestError("無法讀取派工清單") from exc
    if not isinstance(data, dict) or data.get("version") != ASSIGNMENT_VERSION:
        raise RequestError("派工清單版本無效")

    worktree = Path(parse_required_string(data, "worktree")).resolve()
    root = manifest_path.parent
    if repo.resolve() != worktree or worktree.parent != root or manifest_path != root / "assignment.json":
        raise RequestError("派工清單與目前 worktree 不一致")
    result_path = Path(parse_required_string(data, "result_path")).resolve()
    if result_path != root / "result.json":
        raise RequestError("派工結果路徑無效")

    base_data = data.get("base")
    if not isinstance(base_data, dict):
        raise RequestError("派工清單缺少 Git 基準")
    base = BaseContext(
        branch=parse_required_string(base_data, "branch"),
        head_sha=parse_required_string(base_data, "head_sha").lower(),
        remote=parse_required_string(base_data, "remote"),
        remote_branch=parse_required_string(base_data, "remote_branch"),
        github_host=parse_required_string(base_data, "github_host"),
        github_repository=parse_required_string(base_data, "github_repository"),
    )
    coordinator_session_id = validate_session_id(parse_required_string(data, "coordinator_session_id"))
    raw_worker_session_id = data.get("worker_session_id")
    if raw_worker_session_id is None:
        worker_session_id = None
    elif isinstance(raw_worker_session_id, str) and raw_worker_session_id:
        worker_session_id = validate_session_id(raw_worker_session_id)
    else:
        raise RequestError("派工清單的 worker_session_id 無效")
    if coordinator:
        if session_id != coordinator_session_id:
            raise RequestError("目前工作階段不是建立派工的主工作階段")
    elif worker_session_id is None or session_id != worker_session_id:
        raise RequestError("目前子工作階段與派工清單不一致")
    target_class = parse_required_string(data, "target_class")
    target = validate_target(repo, target_class)
    branch = parse_required_string(data, "branch")
    expected_branch = branch_name(coordinator_session_id, target_class, base.head_sha)
    if branch != expected_branch:
        raise RequestError("派工分支名稱與工作識別不一致")
    expected_assignment_id = assignment_digest(coordinator_session_id, target_class, base.head_sha)[:24]
    assignment_id = parse_required_string(data, "assignment_id")
    if assignment_id != expected_assignment_id:
        raise RequestError("派工識別碼無效")
    for key in ("target_source", "candidate_class", "test_file"):
        if parse_required_string(data, key) != target[key]:
            raise RequestError(f"派工清單的 {key} 與目標類別不一致")
    raw_sources = data.get("specification_sources")
    if not isinstance(raw_sources, list) or not raw_sources or any(not isinstance(item, str) for item in raw_sources):
        raise RequestError("派工清單缺少可信規格來源")

    assignment = Assignment(
        assignment_id=assignment_id,
        coordinator_session_id=coordinator_session_id,
        worker_session_id=worker_session_id,
        coordinator_repo=Path(parse_required_string(data, "coordinator_repo")).resolve(),
        common_git_dir=Path(parse_required_string(data, "common_git_dir")).resolve(),
        manifest_path=manifest_path,
        result_path=result_path,
        branch=branch,
        target_class=target_class,
        target_source=target["target_source"],
        candidate_class=target["candidate_class"],
        test_file=target["test_file"],
        specification_sources=tuple(raw_sources),
        base=base,
    )
    verify_assignment_state(repo, assignment, require_base_head=require_base_head)
    return assignment


def validate_candidate_request(
    repo: Path,
    assignment: Assignment,
    data: dict[str, Any],
    *,
    require_cases: bool,
) -> dict[str, Any]:
    cases = validate_test_cases(data) if require_cases else []
    path = PurePosixPath(assignment.test_file)
    target = destination(repo, path)
    if target.is_symlink() or not target.is_file():
        raise RequestError(f"工作代理必須建立唯一測試檔：{assignment.test_file}")
    try:
        content = target.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise RequestError("無法讀取工作代理建立的測試檔") from exc
    if not content.strip():
        raise RequestError("候選測試內容不得為空")
    if len(content.encode("utf-8")) > MAX_FILE_BYTES:
        raise RequestError(f"候選測試不得超過 {MAX_FILE_BYTES} bytes")
    expected_package = assignment.target_class.rpartition(".")[0]
    package_match = PACKAGE.search(content)
    actual_package = package_match.group(1) if package_match else ""
    if actual_package != expected_package:
        raise RequestError(f"package 應為 {expected_package}")
    test_simple_name = assignment.candidate_class.rsplit(".", 1)[-1]
    if not re.search(rf"\bclass\s+{re.escape(test_simple_name)}\b", content):
        raise RequestError(f"候選內容缺少類別 {test_simple_name}")
    missing_ids = [case["id"] for case in cases if case["id"] not in content]
    if missing_ids:
        raise RequestError("候選測試缺少案例編號：" + ", ".join(missing_ids))
    require_only_path(changed_paths(repo), assignment.test_file, "驗證工作代理變更前")
    return {
        "target_class": assignment.target_class,
        "candidate_class": assignment.candidate_class,
        "test_cases": cases,
        "file": {"path": assignment.test_file, "content": content},
    }


# GITHUB AND BASE PREFLIGHT


def github_remote(value: str) -> tuple[str, str]:
    if "://" not in value and re.fullmatch(r"[^@\s]+@[^:\s]+:.+", value):
        user_host, raw_path = value.split(":", 1)
        host = user_host.rsplit("@", 1)[-1]
        path = raw_path
    else:
        parsed = urllib.parse.urlparse(value)
        host = parsed.hostname or ""
        path = parsed.path
    repository = urllib.parse.unquote(path).strip("/").removesuffix(".git")
    if not host or not re.fullmatch(r"[^/\s]+/[^/\s]+", repository):
        raise RequestError("Git push remote 不是可辨識的 GitHub repository URL")
    return host.lower(), repository


def github_environment() -> dict[str, str]:
    return {
        **os.environ,
        "GH_PROMPT_DISABLED": "1",
        "GH_PAGER": "cat",
        "NO_COLOR": "1",
    }


def remote_sha(repo: Path, remote: str, branch: str) -> str | None:
    result = run_command(
        ["git", "-C", str(repo), "ls-remote", "--exit-code", remote, f"refs/heads/{branch}"],
        cwd=repo,
        timeout=GIT_TIMEOUT_SECONDS,
    )
    if result.returncode == 2:
        return None
    if result.returncode != 0:
        raise command_failure(result, f"無法查詢遠端分支 {remote}/{branch}")
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if len(lines) != 1:
        raise RequestError(f"遠端分支 {remote}/{branch} 回傳非預期結果")
    sha, separator, reference = lines[0].partition("\t")
    if separator != "\t" or reference != f"refs/heads/{branch}" or not re.fullmatch(r"[0-9a-fA-F]{40,64}", sha):
        raise RequestError(f"遠端分支 {remote}/{branch} 的 SHA 格式無效")
    return sha.lower()


def require_remote_sha(repo: Path, remote: str, branch: str, expected: str, stage: str) -> None:
    actual = remote_sha(repo, remote, branch)
    if actual != expected:
        raise RequestError(f"{stage}的遠端 {remote}/{branch} 已移動：預期 {expected}，實際 {actual or '(不存在)'}")


def base_context(repo: Path) -> BaseContext:
    status_text = git(repo, "status", "--porcelain=v1", "--untracked-files=all")
    if status_text:
        preview = "；".join(status_text.splitlines()[:5])
        raise RequestError(f"建立工作代理前，基準 worktree 必須沒有未提交變更：{preview}")
    branch = optional_git(repo, "symbolic-ref", "--quiet", "--short", "HEAD")
    if not branch:
        raise RequestError("基準 worktree 不得處於 detached HEAD")
    if branch != TRUSTED_BASE_BRANCH:
        raise RequestError(f"必須從受信任基準分支 {TRUSTED_BASE_BRANCH} 啟動，目前分支為 {branch}")
    head_sha = git(repo, "rev-parse", "HEAD").lower()
    remote = optional_git(repo, "config", "--get", f"branch.{branch}.remote")
    merge_ref = optional_git(repo, "config", "--get", f"branch.{branch}.merge")
    if not remote or remote == "." or not merge_ref or not merge_ref.startswith("refs/heads/"):
        raise RequestError(f"目前分支 {branch} 必須追蹤 GitHub 遠端分支")
    remote_branch = merge_ref.removeprefix("refs/heads/")
    if remote_branch != TRUSTED_BASE_BRANCH:
        raise RequestError(f"基準分支 {branch} 必須追蹤遠端 {TRUSTED_BASE_BRANCH}，目前追蹤 {remote_branch}")
    upstream_sha = git(repo, "rev-parse", "@{upstream}").lower()
    if head_sha != upstream_sha:
        raise RequestError(f"目前 HEAD 與本機追蹤分支 {remote}/{remote_branch} 不一致")
    remote_url = git(repo, "remote", "get-url", "--push", remote)
    host, repository = github_remote(remote_url)
    require_remote_sha(repo, remote, remote_branch, head_sha, "建立工作前")
    if shutil.which("gh") is None:
        raise RequestError("找不到 GitHub CLI gh，無法建立 Draft PR")
    auth = run_command(
        ["gh", "auth", "status", "--hostname", host],
        cwd=repo,
        timeout=GITHUB_TIMEOUT_SECONDS,
        env=github_environment(),
    )
    if auth.returncode != 0:
        raise command_failure(auth, f"GitHub CLI 尚未登入 {host}")
    return BaseContext(branch, head_sha, remote, remote_branch, host, repository)


def git_common_dir(repo: Path) -> Path:
    raw = git(repo, "rev-parse", "--git-common-dir", message="無法確認 Git common directory")
    path = Path(raw)
    return path.resolve() if path.is_absolute() else (repo / path).resolve()


def verify_assignment_state(repo: Path, assignment: Assignment, *, require_base_head: bool) -> None:
    branch = optional_git(repo, "symbolic-ref", "--quiet", "--short", "HEAD")
    if branch != assignment.branch:
        raise RequestError(f"目前 worktree 分支不是派工分支：預期 {assignment.branch}，實際 {branch or '(detached HEAD)'}")
    if git_common_dir(repo) != assignment.common_git_dir:
        raise RequestError("目前 worktree 不屬於派工時的 Git repository")
    if require_base_head:
        head_sha = git(repo, "rev-parse", "HEAD").lower()
        if head_sha != assignment.base.head_sha:
            raise RequestError("派工分支已有未經工具建立的提交，需要人工確認")
    remote_url = git(repo, "remote", "get-url", "--push", assignment.base.remote)
    if github_remote(remote_url) != (assignment.base.github_host, assignment.base.github_repository):
        raise RequestError("Git push remote 與派工清單不一致")
    require_remote_sha(
        repo,
        assignment.base.remote,
        assignment.base.remote_branch,
        assignment.base.head_sha,
        "工作代理驗證時",
    )


# WORKTREE DISPATCH


def ensure_branch_available(repo: Path, base: BaseContext, branch: str) -> None:
    local = run_command(
        ["git", "-C", str(repo), "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
        cwd=repo,
        timeout=GIT_TIMEOUT_SECONDS,
    )
    if local.returncode == 0:
        raise BranchConflictError(f"本機分支已存在，需要人工確認先前工作結果：{branch}")
    if local.returncode != 1:
        raise command_failure(local, f"無法檢查本機分支 {branch}")
    if remote_sha(repo, base.remote, branch) is not None:
        raise BranchConflictError(f"遠端分支已存在，需要人工確認先前提交或 PR：{base.remote}/{branch}")


def create_worktree(repo: Path, base: BaseContext, branch: str) -> Worktree:
    ensure_branch_available(repo, base, branch)
    root = Path(tempfile.mkdtemp(prefix="opencode-unit-test-"))
    project = root / "repo"
    result = run_command(
        ["git", "-C", str(repo), "worktree", "add", "--quiet", "-b", branch, str(project), base.head_sha],
        cwd=repo,
        timeout=GIT_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        shutil.rmtree(root, ignore_errors=True)
        run_command(
            ["git", "-C", str(repo), "branch", "-D", "--", branch],
            cwd=repo,
            timeout=GIT_TIMEOUT_SECONDS,
            allow_cancelled=True,
        )
        raise command_failure(result, f"無法建立測試 worktree {branch}")
    return Worktree(root, project, branch)


def cleanup_worktree(repo: Path, worktree: Worktree, delete_branch: bool) -> list[str]:
    warnings: list[str] = []
    removed = run_command(
        ["git", "-C", str(repo), "worktree", "remove", "--force", str(worktree.project)],
        cwd=repo,
        timeout=GIT_TIMEOUT_SECONDS,
        allow_cancelled=True,
    )
    if removed.returncode != 0:
        warnings.append(
            f"Git 無法移除 worktree；已保留於 {worktree.project}，需要人工執行 git worktree remove --force <path>"
        )
        return warnings
    shutil.rmtree(worktree.root, ignore_errors=True)
    if delete_branch:
        deleted = run_command(
            ["git", "-C", str(repo), "branch", "-D", "--", worktree.branch],
            cwd=repo,
            timeout=GIT_TIMEOUT_SECONDS,
            allow_cancelled=True,
        )
        if deleted.returncode != 0:
            warnings.append(f"無法刪除工具建立的本機分支 {worktree.branch}")
    return warnings


def atomic_write(path: Path, content: str, *, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}-{threading.get_ident()}")
    try:
        temporary.write_text(content, encoding="utf-8")
        if mode is not None:
            temporary.chmod(mode)
        os.replace(temporary, path)
        if mode is not None:
            path.chmod(mode)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_write_json(path: Path, data: dict[str, Any], *, mode: int | None = None) -> None:
    atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n", mode=mode)


def link_opencode_dependencies(source_modules: Path, target_modules: Path) -> None:
    if target_modules.exists() or target_modules.is_symlink():
        raise RequestError("新 worktree 的 .opencode/node_modules 不是空白")
    target_modules.mkdir()
    for source in sorted(source_modules.iterdir(), key=lambda path: path.name):
        destination_path = target_modules / source.name
        if source.is_dir() and source.name.startswith("@"):
            destination_path.mkdir()
            for scoped_source in sorted(source.iterdir(), key=lambda path: path.name):
                (destination_path / scoped_source.name).symlink_to(
                    scoped_source.resolve(),
                    target_is_directory=scoped_source.is_dir(),
                )
        else:
            destination_path.symlink_to(source.resolve(), target_is_directory=source.is_dir())


def prepare_assignment(
    repo: Path,
    base: BaseContext,
    session_id: str,
    target: dict[str, Any],
) -> tuple[Worktree, Assignment]:
    branch = branch_name(session_id, target["target_class"], base.head_sha)
    worktree = create_worktree(repo, base, branch)
    try:
        source_modules = (repo / ".opencode" / "node_modules").resolve()
        plugin_module = source_modules / "@opencode-ai" / "plugin"
        if not plugin_module.exists():
            raise RequestError("基準專案缺少 .opencode/node_modules/@opencode-ai/plugin，無法啟動工作代理")
        target_modules = worktree.project / ".opencode" / "node_modules"
        link_opencode_dependencies(source_modules, target_modules)
        if changed_paths(worktree.project):
            raise RequestError("OpenCode 執行相依套件造成未預期的 Git 變更")

        assignment_id = assignment_digest(session_id, target["target_class"], base.head_sha)[:24]
        manifest_path = worktree.root / "assignment.json"
        result_path = worktree.root / "result.json"
        manifest = {
            "version": ASSIGNMENT_VERSION,
            "assignment_id": assignment_id,
            "coordinator_session_id": session_id,
            "worker_session_id": None,
            "coordinator_repo": str(repo),
            "common_git_dir": str(git_common_dir(repo)),
            "worktree": str(worktree.project),
            "result_path": str(result_path),
            "branch": branch,
            "target_class": target["target_class"],
            "target_source": target["target_source"],
            "candidate_class": target["candidate_class"],
            "test_file": target["test_file"],
            "specification_sources": target["specification_sources"],
            "base": base_to_json(base),
        }
        atomic_write_json(manifest_path, manifest, mode=0o600)
        assignment = Assignment(
            assignment_id=assignment_id,
            coordinator_session_id=session_id,
            worker_session_id=None,
            coordinator_repo=repo,
            common_git_dir=git_common_dir(repo),
            manifest_path=manifest_path,
            result_path=result_path,
            branch=branch,
            target_class=target["target_class"],
            target_source=target["target_source"],
            candidate_class=target["candidate_class"],
            test_file=target["test_file"],
            specification_sources=tuple(target["specification_sources"]),
            base=base,
        )
        return worktree, assignment
    except Exception:
        cleanup_worktree(repo, worktree, delete_branch=True)
        raise


def worker_prompt(assignment: Assignment) -> str:
    sources = "\n".join(f"- {source}" for source in assignment.specification_sources)
    return (
        "你正在由主代理建立的獨立 Git worktree 內執行單一 Service 測試工作。\n"
        f"唯一受測類別：{assignment.target_class}\n"
        f"唯一允許修改的測試檔：{assignment.test_file}\n"
        "可信規格來源：\n"
        f"{sources}\n\n"
        "依 unit-test 代理規則完成案例設計，直接用 edit 或 write 建立或更新上述唯一測試檔。"
        "每次修改後呼叫 validate_unit_tests；依 Maven 與 JaCoCo 結果修正。"
        "最新一次驗證回傳 validation-passed 後直接結束，不得呼叫 submit_unit_tests、提交、推送或建立 PR。"
        "不得處理其他類別，不得使用 task，不得修改正式原始碼或 pom.xml。"
    )


def read_assignment_result(assignment: Assignment) -> dict[str, Any] | None:
    if assignment.result_path.is_file() and not assignment.result_path.is_symlink():
        try:
            raw = json.loads(assignment.result_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and raw.get("assignment_id") == assignment.assignment_id:
                return raw
        except (json.JSONDecodeError, OSError, UnicodeError):
            return None
    return None


def bind_assignment(repo: Path, coordinator_session_id: str, request: dict[str, Any]) -> dict[str, Any]:
    assignment = load_assignment(repo, coordinator_session_id, coordinator=True)
    assignment_id = parse_required_string(request, "assignment_id")
    if assignment_id != assignment.assignment_id:
        raise RequestError("綁定的派工識別碼不一致")
    worker_session_id = validate_session_id(parse_required_string(request, "worker_session_id"))
    if assignment.worker_session_id not in (None, worker_session_id):
        raise RequestError("派工清單已綁定其他子工作階段")
    data = json.loads(assignment.manifest_path.read_text(encoding="utf-8"))
    data["worker_session_id"] = worker_session_id
    atomic_write_json(assignment.manifest_path, data, mode=0o600)
    rebound = load_assignment(repo, worker_session_id)
    return {
        "status": "assignment-bound",
        "assignment_id": rebound.assignment_id,
        "target_class": rebound.target_class,
        "worker_session_id": worker_session_id,
        "worktree": str(repo),
    }


def verified_success(result: dict[str, Any]) -> bool:
    pr = result.get("pr")
    return (
        result.get("status") == "draft-pr-created"
        and result.get("pr_created") is True
        and result.get("pr_verified") is True
        and isinstance(pr, dict)
        and pr.get("draft") is True
        and isinstance(result.get("commit_sha"), str)
        and result.get("commit_sha") == result.get("remote_sha")
    )


def validated_result(result: dict[str, Any]) -> bool:
    validation = result.get("validation")
    candidate_tests = validation.get("candidate_tests") if isinstance(validation, dict) else None
    coverage = validation.get("coverage") if isinstance(validation, dict) else None
    digest = result.get("candidate_sha256")
    return (
        result.get("status") == "validation-passed"
        and result.get("submitted") is False
        and result.get("pr_created") is False
        and isinstance(digest, str)
        and re.fullmatch(r"[0-9a-f]{64}", digest) is not None
        and isinstance(candidate_tests, dict)
        and isinstance(candidate_tests.get("tests"), int)
        and candidate_tests.get("tests", 0) > 0
        and candidate_tests.get("skipped") == 0
        and candidate_tests.get("unexpected_classes") == []
        and isinstance(coverage, dict)
        and coverage.get("passed") is True
    )


def verified_completion(result: dict[str, Any]) -> bool:
    return verified_success(result) or (
        validated_result(result)
        and result.get("post_worker_verified") is True
        and result.get("worktree_retained") is True
    )


def verify_validated_worker(worktree: Worktree, assignment: Assignment, result: dict[str, Any]) -> None:
    if not validated_result(result):
        raise RequestError("工作代理結果不符合本地驗證完成條件")
    if result.get("target_class") != assignment.target_class:
        raise RequestError("工作代理結果的受測類別與派工清單不一致")
    if result.get("test_file") != assignment.test_file:
        raise RequestError("工作代理結果的測試檔與派工清單不一致")
    if result.get("branch") != assignment.branch or result.get("base_sha") != assignment.base.head_sha:
        raise RequestError("工作代理結果的 Git 基準或分支與派工清單不一致")

    verify_assignment_state(worktree.project, assignment, require_base_head=True)
    require_only_path(changed_paths(worktree.project), assignment.test_file, "工作代理完成驗證後")
    candidate = destination(worktree.project, PurePosixPath(assignment.test_file))
    if candidate.is_symlink() or not candidate.is_file():
        raise RequestError("工作代理完成驗證後候選測試不再是一般檔案")
    try:
        digest = hashlib.sha256(candidate.read_text(encoding="utf-8").encode("utf-8")).hexdigest()
    except (OSError, UnicodeError) as exc:
        raise RequestError("工作代理完成驗證後無法讀取候選測試") from exc
    if digest != result["candidate_sha256"]:
        raise RequestError("工作代理在最新一次驗證後又修改了候選測試")
    if remote_sha(worktree.project, assignment.base.remote, assignment.branch) is not None:
        raise RequestError("只驗證模式不應存在遠端派工分支")


def verify_completed_worker(worktree: Worktree, assignment: Assignment, result: dict[str, Any]) -> None:
    if not verified_success(result):
        raise RequestError("工作代理結果不符合 Draft PR 完成條件")
    if result.get("target_class") != assignment.target_class:
        raise RequestError("工作代理結果的受測類別與派工清單不一致")
    if result.get("test_file") != assignment.test_file:
        raise RequestError("工作代理結果的測試檔與派工清單不一致")
    if result.get("branch") != assignment.branch or result.get("base_sha") != assignment.base.head_sha:
        raise RequestError("工作代理結果的 Git 基準或分支與派工清單不一致")
    verify_assignment_state(worktree.project, assignment, require_base_head=False)
    head_sha = git(worktree.project, "rev-parse", "HEAD").lower()
    if head_sha != result["commit_sha"]:
        raise RequestError(f"工作代理退出時 HEAD 已改變：預期 {result['commit_sha']}，實際 {head_sha}")
    if changed_paths(worktree.project):
        raise RequestError("工作代理在發布後又留下未提交變更")
    live_sha = remote_sha(worktree.project, assignment.base.remote, assignment.branch)
    if live_sha != head_sha:
        raise RequestError(f"工作代理退出時遠端分支 SHA 已改變：預期 {head_sha}，實際 {live_sha or '(不存在)'}")

    pr = result["pr"]
    url = pr.get("url")
    if not isinstance(url, str) or not url:
        raise RequestError("工作代理結果缺少 Draft PR URL")
    viewed = run_command(
        [
            "gh",
            "pr",
            "view",
            url,
            "--repo",
            github_locator(assignment.base),
            "--json",
            "url,isDraft,state,headRefName,headRefOid,baseRefName",
        ],
        cwd=worktree.project,
        timeout=GITHUB_TIMEOUT_SECONDS,
        env=github_environment(),
    )
    if viewed.returncode != 0:
        raise command_failure(viewed, "工作代理退出後無法重新查詢 Draft PR")
    try:
        details = json.loads(viewed.stdout)
    except json.JSONDecodeError as exc:
        raise RequestError("工作代理退出後 gh pr view 沒有回傳有效 JSON") from exc
    expected = {
        "url": url,
        "isDraft": True,
        "state": "OPEN",
        "headRefName": assignment.branch,
        "headRefOid": head_sha,
        "baseRefName": assignment.base.remote_branch,
    }
    mismatches = [key for key, value in expected.items() if details.get(key) != value]
    if mismatches:
        raise RequestError("工作代理退出後 Draft PR 核對失敗：" + ", ".join(mismatches))


def prepare_dispatch(repo: Path, session_id: str, request: dict[str, Any]) -> dict[str, Any]:
    try:
        base = base_context(repo)
    except RequestError as exc:
        return {
            "status": "cancelled" if _CANCEL_REQUESTED else "preflight-failed",
            "message": str(exc),
            "prepared": [],
            "results": [],
        }

    prepared: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = [
        {
            "status": "not-started",
            "message": item["reason"],
            "target_class": item["target_class"],
            "test_file": item["test_file"],
            "base_sha": base.head_sha,
            "submitted": False,
            "pr_created": False,
            "merged": False,
            "manual_recovery_required": False,
            "automatic_retry_supported": False,
            "worktree_retained": False,
        }
        for item in request["not_started"]
    ]
    for target in request["targets"]:
        try:
            require_remote_sha(repo, base.remote, base.remote_branch, base.head_sha, "建立 worktree 前")
            worktree, assignment = prepare_assignment(repo, base, session_id, target)
            prepared.append(
                {
                    "assignment_id": assignment.assignment_id,
                    "target_class": assignment.target_class,
                    "test_file": assignment.test_file,
                    "branch": assignment.branch,
                    "base_sha": assignment.base.head_sha,
                    "worktree": str(worktree.project),
                    "prompt": worker_prompt(assignment),
                }
            )
        except (RequestError, OSError, UnicodeError) as exc:
            failures.append(
                {
                    "status": "branch-conflict" if isinstance(exc, BranchConflictError) else "dispatch-failed",
                    "message": str(exc),
                    "target_class": target["target_class"],
                    "test_file": target["test_file"],
                    "base_sha": base.head_sha,
                    "submitted": False,
                    "pr_created": False,
                    "merged": False,
                    "manual_recovery_required": isinstance(exc, BranchConflictError),
                }
            )

    preparation_failures = len(failures) - len(request["not_started"])
    overall = (
        "prepared"
        if len(prepared) == len(request["targets"])
        else "partially-prepared"
        if prepared
        else "preparation-failed"
    )
    return {
        "status": overall,
        "message": (
            f"{len(prepared)} 個 Service worktree 已準備，"
            f"{len(request['not_started'])} 個因規格原因未開始，{preparation_failures} 個未能準備。"
        ),
        "execution_mode": request["execution_mode"],
        "base_branch": base.remote_branch,
        "base_sha": base.head_sha,
        "max_concurrency": request["max_concurrency"],
        "service_count": len(request["target_order"]),
        "target_order": request["target_order"],
        "prepared": prepared,
        "results": failures,
    }


def optional_limited_string(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise RequestError(f"{key} 必須是字串")
    return value.strip()[-4_000:]


def finalize_assignment(repo: Path, coordinator_session_id: str, request: dict[str, Any]) -> dict[str, Any]:
    assignment = load_assignment(
        repo,
        coordinator_session_id,
        coordinator=True,
        require_base_head=False,
    )
    if parse_required_string(request, "assignment_id") != assignment.assignment_id:
        raise RequestError("結束工作的派工識別碼不一致")
    supplied_worker_session_id = request.get("worker_session_id")
    if supplied_worker_session_id is not None:
        if not isinstance(supplied_worker_session_id, str):
            raise RequestError("worker_session_id 必須是字串")
        supplied_worker_session_id = validate_session_id(supplied_worker_session_id)
    if assignment.worker_session_id is not None and supplied_worker_session_id != assignment.worker_session_id:
        raise RequestError("結束工作的子工作階段與派工清單不一致")
    cancelled = request.get("cancelled", False)
    if not isinstance(cancelled, bool):
        raise RequestError("cancelled 必須是布林值")
    worker_message = optional_limited_string(request, "worker_message")
    worker_error = optional_limited_string(request, "worker_error")

    result = read_assignment_result(assignment)
    if result is None:
        status_value = (
            "cancelled"
            if cancelled
            else "worker-session-create-failed"
            if supplied_worker_session_id is None
            else "worker-failed"
            if worker_error
            else "worker-finished-without-validation"
        )
        message = (
            "子工作階段已取消，尚未取得可核對的驗證結果。"
            if cancelled
            else "無法建立或綁定子工作階段。"
            if supplied_worker_session_id is None
            else "子工作階段執行失敗，尚未取得可核對的驗證結果。"
            if worker_error
            else "子工作階段已結束，但沒有呼叫驗證工具或沒有產生可核對的驗證結果。"
        )
        result = {
            "status": status_value,
            "message": message,
            "assignment_id": assignment.assignment_id,
            "target_class": assignment.target_class,
            "test_file": assignment.test_file,
            "base_sha": assignment.base.head_sha,
            "branch": assignment.branch,
            "submitted": False,
            "pr_created": False,
            "merged": False,
            "manual_recovery_required": False,
            "automatic_retry_supported": False,
        }

    if supplied_worker_session_id:
        result["worker_session_id"] = supplied_worker_session_id
    if worker_message:
        result["worker_message"] = worker_message
    if worker_error:
        result["worker_error"] = worker_error

    worktree = Worktree(root=repo.parent, project=repo, branch=assignment.branch)
    if validated_result(result):
        try:
            verify_validated_worker(worktree, assignment, result)
        except (RequestError, OSError, UnicodeError) as exc:
            result["validated_status"] = result["status"]
            result["status"] = "post-worker-verification-failed"
            result["message"] = str(exc)
            result["manual_recovery_required"] = True
            result["automatic_retry_supported"] = False
        else:
            result["post_worker_verified"] = True
    elif verified_success(result):
        try:
            verify_completed_worker(worktree, assignment, result)
        except (RequestError, OSError, UnicodeError) as exc:
            result["published_status"] = result["status"]
            result["status"] = "post-worker-verification-failed"
            result["message"] = str(exc)
            result["manual_recovery_required"] = True
            result["automatic_retry_supported"] = False
        else:
            result["post_worker_verified"] = True

    if verified_success(result):
        warnings = cleanup_worktree(assignment.coordinator_repo, worktree, delete_branch=True)
        retained = worktree.project.exists()
        result["worktree_retained"] = retained
        if warnings:
            result["cleanup_warnings"] = warnings
        if retained:
            result["worktree"] = str(worktree.project)
    else:
        result["worktree_retained"] = worktree.project.exists()
        if worktree.project.exists():
            result["worktree"] = str(worktree.project)
    return result


# CHANGE AND MAVEN VALIDATION


def git_nul_paths(repo: Path, *arguments: str) -> set[str]:
    result = run_command(["git", "-C", str(repo), *arguments], cwd=repo, timeout=GIT_TIMEOUT_SECONDS)
    if result.returncode != 0:
        raise command_failure(result, "無法檢查 Git 變更清單")
    return {value for value in result.stdout.split("\0") if value}


def changed_paths(repo: Path) -> set[str]:
    return (
        git_nul_paths(repo, "diff", "--name-only", "-z", "--")
        | git_nul_paths(repo, "diff", "--cached", "--name-only", "-z", "--")
        | git_nul_paths(repo, "ls-files", "--others", "--exclude-standard", "-z")
    )


def require_only_path(actual: set[str], expected: str, stage: str) -> None:
    if actual != {expected}:
        shown = ", ".join(sorted(actual)) if actual else "(沒有變更)"
        raise RequestError(f"{stage}的 Git 變更必須只有 {expected}：{shown}")


def clear_maven_outputs(project: Path) -> None:
    target = project / "target"
    if target.is_symlink():
        raise RequestError("target 不得是符號連結")
    if target.exists() and not target.is_dir():
        raise RequestError("target 不是目錄")
    if target.is_dir():
        shutil.rmtree(target)


def run_maven(project: Path, candidate_class: str) -> dict[str, Any]:
    allowed_environment = {
        "HOME",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "JAVA_HOME",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "LOGNAME",
        "MAVEN_ARGS",
        "MAVEN_OPTS",
        "MAVEN_USER_HOME",
        "NO_PROXY",
        "PATH",
        "SSL_CERT_DIR",
        "SSL_CERT_FILE",
        "TMPDIR",
        "USER",
        "http_proxy",
        "https_proxy",
        "no_proxy",
    }
    environment = {name: value for name, value in os.environ.items() if name in allowed_environment}
    environment.update({"CI": "true", "TERM": "dumb", "PWD": str(project)})
    isolated_config = project.parent / "validation-config"
    (isolated_config / "gh").mkdir(mode=0o700, parents=True, exist_ok=True)
    environment.update(
        {
            "GH_CONFIG_DIR": str(isolated_config / "gh"),
            "GH_PROMPT_DISABLED": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    result = run_command(
        [str(project / "mvnw"), "-B", "-ntp", f"-Dtest={candidate_class}", "test"],
        cwd=project,
        timeout=MAVEN_TIMEOUT_SECONDS,
        env=environment,
    )
    output_lines = result.stdout.splitlines() + result.stderr.splitlines()
    return {
        "command": f"./mvnw -B -ntp -Dtest={candidate_class} test",
        "exit_code": result.returncode,
        "timed_out": result.returncode == 124,
        "maven_errors": "\n".join(line for line in output_lines if "[ERROR]" in line),
    }


def test_summary(project: Path, candidate_class: str) -> dict[str, Any]:
    tests = skipped = 0
    reports: list[str] = []
    unexpected_classes: set[str] = set()
    for report in sorted((project / "target/surefire-reports").glob("TEST-*.xml")):
        try:
            root = ET.parse(report).getroot()
        except (ET.ParseError, OSError) as exc:
            raise RequestError(f"無法解析 Maven 測試報告：{report.name}") from exc
        matched = False
        for case in root.iter():
            if case.tag.rsplit("}", 1)[-1] != "testcase":
                continue
            class_name = case.attrib.get("classname", "")
            if class_name != candidate_class and not class_name.startswith(candidate_class + "$"):
                if class_name:
                    unexpected_classes.add(class_name)
                continue
            matched, tests = True, tests + 1
            skipped += any(child.tag.rsplit("}", 1)[-1] == "skipped" for child in case)
        if matched:
            reports.append(report.name)
    return {
        "class": candidate_class,
        "tests": tests,
        "executed": tests - skipped,
        "skipped": skipped,
        "reports": reports,
        "unexpected_classes": sorted(unexpected_classes),
    }


def coverage_summary(project: Path, target_class: str) -> dict[str, Any]:
    report = project / "target" / "site" / "jacoco" / "jacoco.xml"
    execution_data = project / "target" / "jacoco.exec"
    if not execution_data.is_file():
        raise RequestError("Maven test 結束，但找不到 target/jacoco.exec")
    if not report.is_file():
        raise RequestError("Maven test 成功，但找不到 target/site/jacoco/jacoco.xml")
    try:
        root = ET.parse(report).getroot()
    except (ET.ParseError, OSError) as exc:
        raise RequestError("無法解析 target/site/jacoco/jacoco.xml") from exc
    target_name = target_class.replace(".", "/")
    target = next(
        (node for node in root.iter() if node.tag.rsplit("}", 1)[-1] == "class" and node.attrib.get("name") == target_name),
        None,
    )
    if target is None:
        raise RequestError(f"JaCoCo XML 找不到受測正式類別：{target_class}")
    counter = next(
        (node for node in target if node.tag.rsplit("}", 1)[-1] == "counter" and node.attrib.get("type") == "LINE"),
        None,
    )
    if counter is None:
        raise RequestError(f"JaCoCo XML 沒有 {target_class} 的 LINE counter")
    try:
        missed = int(counter.attrib["missed"])
        covered = int(counter.attrib["covered"])
    except (KeyError, ValueError) as exc:
        raise RequestError(f"JaCoCo XML 的 {target_class} LINE counter 無效") from exc
    total = missed + covered
    if total == 0:
        raise RequestError(f"JaCoCo 無法計算 {target_class} 的行覆蓋率")
    package_name = target_name.rpartition("/")[0]
    source_name = target.attrib.get("sourcefilename")
    source = next(
        (
            child
            for package in root.iter()
            if package.tag.rsplit("}", 1)[-1] == "package" and package.attrib.get("name") == package_name
            for child in package
            if child.tag.rsplit("}", 1)[-1] == "sourcefile" and child.attrib.get("name") == source_name
        ),
        None,
    )
    missed_lines: list[int] = []
    if source is not None:
        try:
            missed_lines = sorted(
                int(line.attrib["nr"])
                for line in source
                if line.tag.rsplit("}", 1)[-1] == "line"
                and int(line.attrib["mi"]) > 0
                and int(line.attrib["ci"]) == 0
            )
        except (KeyError, ValueError) as exc:
            raise RequestError(f"JaCoCo XML 的 {target_class} 行號資料無效") from exc
    percent = covered * 100 / total
    return {
        "target_class": target_class,
        "counter": "LINE",
        "covered": covered,
        "missed": missed,
        "percent": round(percent, 2),
        "minimum_percent": MINIMUM_LINE_COVERAGE_PERCENT,
        "passed": covered * 100 >= MINIMUM_LINE_COVERAGE_PERCENT * total,
        "missed_lines": missed_lines,
        "xml": "target/site/jacoco/jacoco.xml",
        "exec": "target/jacoco.exec",
    }


def validation_failure(status_value: str, message: str, validation: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {
        "status": status_value,
        "message": message,
        "submitted": False,
        "pr_created": False,
        "merged": False,
        "validation": validation,
        **extra,
    }


def validate_candidate(project: Path, assignment: Assignment, request: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    expected_path = assignment.test_file
    before = hashlib.sha256(request["file"]["content"].encode("utf-8")).hexdigest()
    clear_maven_outputs(project)
    maven = run_maven(project, assignment.candidate_class)
    require_only_path(changed_paths(project), expected_path, "Maven 驗證後")
    current = destination(project, PurePosixPath(expected_path))
    if current.is_symlink() or not current.is_file():
        raise RequestError("Maven 驗證後候選測試不再是一般檔案")
    after_content = current.read_text(encoding="utf-8")
    after = hashlib.sha256(after_content.encode("utf-8")).hexdigest()
    if after != before:
        raise RequestError("Maven 驗證後的候選測試內容與驗證前不一致")
    validation = {key: maven[key] for key in ("command", "exit_code", "timed_out")}
    if maven["exit_code"] == 130 and _CANCEL_REQUESTED:
        return None, validation_failure("cancelled", "單元測試工作已取消；沒有推送分支或建立 PR。", validation)
    if maven["exit_code"] != 0:
        return None, validation_failure(
            "candidate-check-failed",
            "候選測試未通過本機 Maven test；請先修正測試編譯或設定錯誤。若可信規格與實作衝突，不得修改預期結果迎合實作。",
            validation,
            diagnostic_field="maven_errors",
            agent_action="修正目前 worktree 內的唯一候選測試檔，再呼叫 validate_unit_tests。",
            maven_errors=maven["maven_errors"],
        )
    summary = test_summary(project, assignment.candidate_class)
    validation["candidate_tests"] = summary
    if summary["tests"] == 0 or summary["skipped"]:
        return None, validation_failure("candidate-not-executed", "Maven 成功，但候選測試沒有全部實際執行。", validation)
    if summary["unexpected_classes"]:
        return None, validation_failure(
            "candidate-not-isolated",
            "Maven 執行了候選類別以外的測試，無法單獨計算候選測試覆蓋率。",
            validation,
        )
    try:
        coverage = coverage_summary(project, assignment.target_class)
    except RequestError as exc:
        return None, validation_failure("coverage-report-invalid", str(exc), validation)
    validation["coverage"] = coverage
    if not coverage["passed"]:
        return None, validation_failure(
            "coverage-below-threshold",
            f"候選測試對 {assignment.target_class} 的行覆蓋率為 {coverage['percent']:.2f}%，低於 {coverage['minimum_percent']}% 門檻。",
            validation,
        )
    return validation, None


def validate_action(repo: Path, assignment: Assignment, request: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any]
    try:
        verify_assignment_state(repo, assignment, require_base_head=True)
        validation, failure = validate_candidate(repo, assignment, request)
        if failure is not None:
            result = {
                **failure,
                "assignment_id": assignment.assignment_id,
                "target_class": assignment.target_class,
                "test_file": assignment.test_file,
                "base_sha": assignment.base.head_sha,
                "branch": assignment.branch,
            }
        else:
            assert validation is not None
            digest = hashlib.sha256(request["file"]["content"].encode("utf-8")).hexdigest()
            result = {
                "status": "validation-passed",
                "message": "候選測試已通過 Maven，且 JaCoCo 目標類別行覆蓋率達到門檻；內容保留在本機分支與 worktree，沒有提交、推送或建立 PR。",
                "assignment_id": assignment.assignment_id,
                "target_class": assignment.target_class,
                "test_file": assignment.test_file,
                "base_sha": assignment.base.head_sha,
                "branch": assignment.branch,
                "candidate_sha256": digest,
                "submitted": False,
                "pr_created": False,
                "merged": False,
                "validation": validation,
            }
    except (RequestError, OSError, UnicodeError) as exc:
        result = {
            "status": "cancelled" if _CANCEL_REQUESTED else "validation-failed",
            "message": str(exc),
            "assignment_id": assignment.assignment_id,
            "target_class": assignment.target_class,
            "test_file": assignment.test_file,
            "base_sha": assignment.base.head_sha,
            "branch": assignment.branch,
            "submitted": False,
            "pr_created": False,
            "merged": False,
        }
    write_assignment_result(assignment, result)
    return result


# COMMIT, PUSH, AND DRAFT PR


def commit_candidate(project: Path, assignment: Assignment, request: dict[str, Any]) -> tuple[str, str]:
    path = assignment.test_file
    content = request["file"]["content"]
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    git(project, "add", "--", path, message="無法暫存候選測試")
    require_only_path(git_nul_paths(project, "diff", "--cached", "--name-only", "-z", "--"), path, "建立提交前")
    if git_nul_paths(project, "diff", "--name-only", "-z", "--") or git_nul_paths(
        project, "ls-files", "--others", "--exclude-standard", "-z"
    ):
        raise RequestError("建立提交前仍有未暫存或未追蹤的額外變更")
    git(project, "diff", "--cached", "--check", "--", message="候選測試未通過 git diff --check")
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
    head_sha = git(project, "rev-parse", "HEAD").lower()
    if git(project, "rev-parse", "HEAD^").lower() != assignment.base.head_sha:
        raise RequestError("候選測試提交的父提交不是已驗證的 base SHA")
    require_only_path(
        git_nul_paths(project, "diff-tree", "--no-commit-id", "--name-only", "-r", "-z", "HEAD"),
        path,
        "候選測試提交",
    )
    shown = run_command(["git", "-C", str(project), "show", f"{head_sha}:{path}"], cwd=project, timeout=GIT_TIMEOUT_SECONDS)
    if shown.returncode != 0:
        raise command_failure(shown, "無法讀取候選測試提交內容")
    if hashlib.sha256(shown.stdout.encode("utf-8")).hexdigest() != digest:
        raise RequestError("候選測試提交內容的 SHA-256 與本機驗證內容不一致")
    if changed_paths(project):
        raise RequestError("建立提交後 worktree 仍有額外變更")
    return head_sha, digest


def push_branch(project: Path, base: BaseContext, branch: str) -> None:
    result = run_command(
        ["git", "-C", str(project), "push", "--porcelain", base.remote, f"HEAD:refs/heads/{branch}"],
        cwd=project,
        timeout=GIT_TIMEOUT_SECONDS,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
    if result.returncode != 0:
        raise command_failure(result, f"無法推送分支 {branch}")


def github_locator(base: BaseContext) -> str:
    return base.github_repository if base.github_host == "github.com" else f"{base.github_host}/{base.github_repository}"


def pr_body(
    request: dict[str, Any],
    validation: dict[str, Any],
    assignment: Assignment,
    head_sha: str,
    candidate_digest: str,
) -> str:
    cases = []
    for case in request["test_cases"]:
        cases.append(
            f"### {case['id']}\n\n- 情境：{case['scenario']}\n- 預期：{case['expected']}\n- 規格依據：{case['evidence']}"
        )
    base = assignment.base
    body = (
        "## 單元測試候選\n\n"
        f"- 受測類別：`{assignment.target_class}`\n"
        f"- 測試檔：`{assignment.test_file}`\n"
        f"- 基準：`{base.remote_branch}` (`{base.head_sha}`)\n"
        f"- 分支：`{assignment.branch}`\n"
        f"- 提交：`{head_sha}`\n"
        f"- 候選內容 SHA-256：`{candidate_digest}`\n\n"
        "## 本機驗證\n\n"
        f"- 指令：`{validation['command']}`\n"
        f"- 實際執行測試：{validation['candidate_tests']['executed']}\n"
        f"- 目標類別行覆蓋率：{validation['coverage']['percent']:.2f}%（門檻 {validation['coverage']['minimum_percent']}%）\n\n"
        "## 測試案例與依據\n\n"
        + "\n\n".join(cases)
        + "\n\n---\n\n此 PR 必須由工程師審查。自動化工具不會將它轉為 Ready，也不會合併。\n"
    )
    if len(body.encode("utf-8")) > MAX_PR_BODY_BYTES:
        raise RequestError(f"Draft PR 內容超過 {MAX_PR_BODY_BYTES} bytes，請縮短案例描述或規格依據")
    return body


def create_draft_pr(
    project: Path,
    request: dict[str, Any],
    validation: dict[str, Any],
    assignment: Assignment,
    head_sha: str,
    candidate_digest: str,
) -> dict[str, Any]:
    body = pr_body(request, validation, assignment, head_sha, candidate_digest)
    base = assignment.base
    locator = github_locator(base)
    title = f"test: 新增 {assignment.target_class} 單元測試"
    with tempfile.TemporaryDirectory(prefix="opencode-unit-test-pr-") as temporary:
        body_file = Path(temporary) / "body.md"
        atomic_write(body_file, body)
        created = run_command(
            [
                "gh",
                "pr",
                "create",
                "--draft",
                "--repo",
                locator,
                "--base",
                base.remote_branch,
                "--head",
                assignment.branch,
                "--title",
                title,
                "--body-file",
                str(body_file),
            ],
            cwd=project,
            timeout=GITHUB_TIMEOUT_SECONDS,
            env=github_environment(),
        )
    if created.returncode != 0:
        failure = command_failure(created, f"分支已推送，但 gh 未能確認 Draft PR 結果：{assignment.branch}")
        raise DraftPrStateUnknownError(str(failure))
    urls = re.findall(r"https?://[^\s]+", created.stdout)
    if not urls:
        raise DraftPrStateUnknownError("gh pr create 結束，但沒有回傳可驗證的 PR URL")
    url = urls[-1].rstrip(".,)")
    viewed = run_command(
        [
            "gh",
            "pr",
            "view",
            url,
            "--repo",
            locator,
            "--json",
            "number,url,isDraft,state,headRefName,headRefOid,baseRefName",
        ],
        cwd=project,
        timeout=GITHUB_TIMEOUT_SECONDS,
        env=github_environment(),
    )
    if viewed.returncode != 0:
        failure = command_failure(viewed, "Draft PR 已建立，但無法重新查詢驗證")
        raise DraftPrVerificationError(str(failure), url)
    try:
        details = json.loads(viewed.stdout)
    except json.JSONDecodeError as exc:
        raise DraftPrVerificationError("gh pr view 沒有回傳有效 JSON", url) from exc
    expected = {
        "url": url,
        "isDraft": True,
        "state": "OPEN",
        "headRefName": assignment.branch,
        "headRefOid": head_sha,
        "baseRefName": base.remote_branch,
    }
    mismatches = [key for key, value in expected.items() if details.get(key) != value]
    if mismatches:
        raise DraftPrVerificationError("Draft PR 驗證失敗：" + ", ".join(mismatches), url)
    return details


def compare_url(base: BaseContext, branch: str) -> str:
    old = urllib.parse.quote(base.remote_branch, safe="")
    new = urllib.parse.quote(branch, safe="")
    return f"https://{base.github_host}/{base.github_repository}/compare/{old}...{new}?expand=1"


def write_assignment_result(assignment: Assignment, result: dict[str, Any]) -> None:
    result["assignment_id"] = assignment.assignment_id
    atomic_write_json(assignment.result_path, result, mode=0o600)


def submit(repo: Path, assignment: Assignment, request: dict[str, Any]) -> dict[str, Any]:
    committed = False
    pushed = False
    head_sha: str | None = None
    validation: dict[str, Any] | None = None
    result: dict[str, Any]
    try:
        verify_assignment_state(repo, assignment, require_base_head=True)
        if remote_sha(repo, assignment.base.remote, assignment.branch) is not None:
            raise BranchConflictError(f"遠端派工分支已存在，需要人工確認：{assignment.base.remote}/{assignment.branch}")
        validation, failure = validate_candidate(repo, assignment, request)
        if failure is not None:
            result = {
                **failure,
                "target_class": assignment.target_class,
                "test_file": assignment.test_file,
                "branch": assignment.branch,
                "base_sha": assignment.base.head_sha,
            }
        else:
            assert validation is not None
            require_remote_sha(
                repo,
                assignment.base.remote,
                assignment.base.remote_branch,
                assignment.base.head_sha,
                "Maven 驗證後",
            )
            head_sha, digest = commit_candidate(repo, assignment, request)
            committed = True
            pr_body(request, validation, assignment, head_sha, digest)
            try:
                push_branch(repo, assignment.base, assignment.branch)
            except RequestError as exc:
                if _CANCEL_REQUESTED:
                    result = {
                        "status": "cancelled",
                        "message": "工作在推送期間取消；遠端分支狀態尚未確認。",
                        "submitted": None,
                        "remote_state": "unknown",
                        "pr_created": False,
                        "merged": False,
                        "manual_recovery_required": True,
                        "automatic_retry_supported": False,
                        "target_class": assignment.target_class,
                        "test_file": assignment.test_file,
                        "branch": assignment.branch,
                        "base_sha": assignment.base.head_sha,
                        "commit_sha": head_sha,
                        "validation": validation,
                    }
                else:
                    observed_remote_sha: str | None = None
                    remote_state = "unknown-after-push-attempt"
                    try:
                        observed_remote_sha = remote_sha(repo, assignment.base.remote, assignment.branch)
                        remote_state = "verified-after-push-attempt"
                    except RequestError:
                        pass
                    result = {
                        "status": "push-failed",
                        "message": str(exc),
                        "submitted": observed_remote_sha == head_sha if remote_state == "verified-after-push-attempt" else None,
                        "remote_sha": observed_remote_sha,
                        "remote_state": remote_state,
                        "pr_created": False,
                        "merged": False,
                        "manual_recovery_required": True,
                        "automatic_retry_supported": False,
                        "target_class": assignment.target_class,
                        "test_file": assignment.test_file,
                        "branch": assignment.branch,
                        "base_sha": assignment.base.head_sha,
                        "commit_sha": head_sha,
                        "validation": validation,
                    }
                    if observed_remote_sha == head_sha:
                        result["compare_url"] = compare_url(assignment.base, assignment.branch)
            else:
                pushed = True
                live_sha = remote_sha(repo, assignment.base.remote, assignment.branch)
                if live_sha != head_sha:
                    raise RequestError(f"推送後遠端分支 SHA 不一致：預期 {head_sha}，實際 {live_sha or '(不存在)'}")
                require_remote_sha(
                    repo,
                    assignment.base.remote,
                    assignment.base.remote_branch,
                    assignment.base.head_sha,
                    "建立 PR 前",
                )
                pr: dict[str, Any] | None = None
                verified_sha: str | None = None
                try:
                    pr = create_draft_pr(repo, request, validation, assignment, head_sha, digest)
                    verified_sha = remote_sha(repo, assignment.base.remote, assignment.branch)
                    if verified_sha != head_sha:
                        raise RequestError(f"PR 建立後遠端分支 SHA 不一致：預期 {head_sha}，實際 {verified_sha or '(不存在)'}")
                    require_remote_sha(
                        repo,
                        assignment.base.remote,
                        assignment.base.remote_branch,
                        assignment.base.head_sha,
                        "PR 建立後",
                    )
                except RequestError as exc:
                    pr_url = pr["url"] if pr is not None else getattr(exc, "pr_url", None)
                    cancelled = _CANCEL_REQUESTED
                    pr_state_unknown = cancelled or isinstance(exc, DraftPrStateUnknownError)
                    result = {
                        "status": "cancelled" if cancelled else "pr-create-or-verify-failed",
                        "message": "工作在建立或驗證 PR 期間取消；PR 狀態尚未確認。" if cancelled else str(exc),
                        "submitted": True,
                        "pr_created": None if pr_state_unknown and pr_url is None else pr_url is not None,
                        "pr_verified": False,
                        "merged": False,
                        "manual_recovery_required": True,
                        "automatic_retry_supported": False,
                        "target_class": assignment.target_class,
                        "test_file": assignment.test_file,
                        "branch": assignment.branch,
                        "base_sha": assignment.base.head_sha,
                        "commit_sha": head_sha,
                        "remote_sha": verified_sha,
                        "compare_url": compare_url(assignment.base, assignment.branch),
                        "validation": validation,
                    }
                    if verified_sha is None:
                        result["remote_state"] = "unknown-after-pr-attempt"
                    if pr_state_unknown and pr_url is None:
                        result["pr_state"] = "unknown"
                    if pr_url is not None:
                        result["pr_url"] = pr_url
                else:
                    result = {
                        "status": "draft-pr-created",
                        "message": "候選測試已通過本機 Maven 與 JaCoCo 驗證，並建立等待人工審查的 Draft PR。",
                        "submitted": True,
                        "pr_created": True,
                        "pr_verified": True,
                        "merged": False,
                        "target_class": assignment.target_class,
                        "test_file": assignment.test_file,
                        "base_branch": assignment.base.remote_branch,
                        "base_sha": assignment.base.head_sha,
                        "branch": assignment.branch,
                        "commit_sha": head_sha,
                        "remote_sha": verified_sha,
                        "pr": {"number": pr["number"], "url": pr["url"], "draft": pr["isDraft"]},
                        "validation": validation,
                    }
    except (RequestError, OSError, UnicodeError) as exc:
        branch_conflict = isinstance(exc, BranchConflictError)
        result = {
            "status": "cancelled" if _CANCEL_REQUESTED else "branch-conflict" if branch_conflict else "submission-failed",
            "message": str(exc),
            "submitted": pushed,
            "pr_created": False,
            "merged": False,
            "target_class": assignment.target_class,
            "test_file": assignment.test_file,
            "branch": assignment.branch,
            "base_sha": assignment.base.head_sha,
            "manual_recovery_required": committed or branch_conflict,
            "automatic_retry_supported": not (committed or branch_conflict),
        }
        if head_sha is not None:
            result["commit_sha"] = head_sha
        if validation is not None:
            result["validation"] = validation
        if pushed:
            result["compare_url"] = compare_url(assignment.base, assignment.branch)
    write_assignment_result(assignment, result)
    return result


# CLI


def main() -> int:
    install_signal_handlers()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "bind", "finalize", "validate", "submit"))
    parser.add_argument("--repo", default=".")
    parser.add_argument("--session-id", required=True)
    args = parser.parse_args()
    assignment: Assignment | None = None
    try:
        repo = repo_root(args.repo)
        session_id = validate_session_id(args.session_id)
        data = read_input()
        if args.action == "prepare":
            request = validate_dispatch_request(repo, data)
            result = prepare_dispatch(repo, session_id, request)
            successful = result["status"] == "prepared"
        elif args.action == "bind":
            result = bind_assignment(repo, session_id, data)
            successful = result["status"] == "assignment-bound"
        elif args.action == "finalize":
            result = finalize_assignment(repo, session_id, data)
            successful = verified_completion(result)
        else:
            assignment = load_assignment(repo, session_id)
            request = validate_candidate_request(repo, assignment, data, require_cases=args.action == "submit")
            if args.action == "validate":
                result = validate_action(repo, assignment, request)
                successful = result["status"] == "validation-passed"
            else:
                result = submit(repo, assignment, request)
                successful = result["status"] == "draft-pr-created"
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if successful else 3
    except (RequestError, OSError, UnicodeError) as exc:
        result = {
            "status": "invalid-request",
            "message": str(exc),
            "submitted": False,
            "pr_created": False,
            "merged": False,
        }
        if assignment is not None and args.action == "submit":
            result.update(
                {
                    "target_class": assignment.target_class,
                    "test_file": assignment.test_file,
                    "branch": assignment.branch,
                    "base_sha": assignment.base.head_sha,
                }
            )
            write_assignment_result(assignment, result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2
    except Exception as exc:  # noqa: BLE001 - CLI 邊界必須回傳結構化錯誤
        result = {
            "status": "internal-error",
            "message": str(exc),
            "submitted": False,
            "pr_created": False,
            "merged": False,
        }
        if assignment is not None and args.action == "submit":
            result.update(
                {
                    "target_class": assignment.target_class,
                    "test_file": assignment.test_file,
                    "branch": assignment.branch,
                    "base_sha": assignment.base.head_sha,
                }
            )
            write_assignment_result(assignment, result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
