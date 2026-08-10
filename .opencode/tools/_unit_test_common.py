"""三個單元測試工具共用的資料契約與安全檢查。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import stat
import subprocess
import sys
import threading
import urllib.parse
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

ASSIGNMENT_VERSION = 2
BRANCH_PREFIX = "opencode/unit-test"
GIT_TIMEOUT_SECONDS = 120
GITHUB_TIMEOUT_SECONDS = 120
MAX_FILE_BYTES = 100_000
MAX_TARGETS = 50
TRUSTED_BASE_BRANCH = "main"
BATCH_EXECUTION_MODE = "unit-test-all/v2"
NOT_STARTED_REASONS = {"缺少可信規格證據", "可信規格彼此衝突"}

ASSIGNMENT_ID = re.compile(r"^[0-9a-f]{24}$")
CASE_ID = re.compile(r"^UT-[0-9]{3,}$")
JAVA_CLASS = re.compile(r"^[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)+$")
PACKAGE = re.compile(
    r"^\s*package\s+([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)\s*;",
    re.MULTILINE,
)
SESSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,200}$")

_ACTIVE_PROCESSES: set[subprocess.Popen[str]] = set()
_ACTIVE_PROCESSES_LOCK = threading.RLock()
_CANCEL_REQUESTED = False


class RequestError(RuntimeError):
    """可安全回傳給代理的輸入或環境錯誤。"""


class BranchConflictError(RequestError):
    """本機或遠端已有同名派工分支。"""


@dataclass(frozen=True)
class BaseContext:
    branch: str
    head_sha: str
    remote: str
    remote_branch: str
    github_host: str
    github_repository: str


@dataclass(frozen=True)
class Assignment:
    assignment_id: str
    coordinator_session_id: str
    worker_session_id: str | None
    coordinator_repo: Path
    state_path: Path
    worktree: Path
    branch: str
    target_class: str
    target_source: str
    candidate_class: str
    test_file: str
    specification_sources: tuple[str, ...]
    base: BaseContext
    state: dict[str, Any]


def cancelled() -> bool:
    return _CANCEL_REQUESTED


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


def optional_git(repo: Path, *arguments: str) -> str | None:
    result = run_command(
        ["git", "-C", str(repo), *arguments],
        cwd=repo,
        timeout=GIT_TIMEOUT_SECONDS,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def read_input() -> dict[str, Any]:
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        raise RequestError(f"輸入不是有效 JSON：{exc}") from exc
    if not isinstance(data, dict):
        raise RequestError("輸入必須是 JSON 物件")
    return data


def validate_session_id(value: str) -> str:
    if not SESSION_ID.fullmatch(value):
        raise RequestError("OpenCode 工作階段識別碼格式無效")
    return value


def parse_required_string(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RequestError(f"缺少有效欄位：{key}")
    return value.strip()


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


def destination(root: Path, relative: PurePosixPath) -> Path:
    target = root.joinpath(*relative.parts)
    if not target.resolve(strict=False).is_relative_to(root.resolve()):
        raise RequestError(f"路徑離開專案範圍：{relative}")
    return target


def git_common_dir(repo: Path) -> Path:
    raw = git(
        repo, "rev-parse", "--git-common-dir", message="無法確認 Git common directory"
    )
    path = Path(raw)
    return path.resolve() if path.is_absolute() else (repo / path).resolve()


def state_directory(repo: Path) -> Path:
    return git_common_dir(repo) / "opencode-unit-tests" / "assignments"


def atomic_write(path: Path, content: str, *, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(
        f".{path.name}.tmp-{os.getpid()}-{threading.get_ident()}"
    )
    try:
        temporary.write_text(content, encoding="utf-8")
        if mode is not None:
            temporary.chmod(mode)
        os.replace(temporary, path)
        if mode is not None:
            path.chmod(mode)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_write_json(path: Path, data: dict[str, Any], *, mode: int = 0o600) -> None:
    atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n", mode=mode)


def base_to_json(base: BaseContext) -> dict[str, str]:
    return {
        "branch": base.branch,
        "head_sha": base.head_sha,
        "remote": base.remote,
        "remote_branch": base.remote_branch,
        "github_host": base.github_host,
        "github_repository": base.github_repository,
    }


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


def remote_sha(
    repo: Path,
    remote: str,
    branch: str,
) -> str | None:
    result = run_command(
        [
            "git",
            "-C",
            str(repo),
            "ls-remote",
            "--exit-code",
            remote,
            f"refs/heads/{branch}",
        ],
        cwd=repo,
        timeout=GIT_TIMEOUT_SECONDS,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
    if result.returncode == 2:
        return None
    if result.returncode != 0:
        raise command_failure(result, f"無法查詢遠端分支 {remote}/{branch}")
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if len(lines) != 1:
        raise RequestError(f"遠端分支 {remote}/{branch} 回傳非預期結果")
    sha, separator, reference = lines[0].partition("\t")
    if (
        separator != "\t"
        or reference != f"refs/heads/{branch}"
        or re.fullmatch(r"[0-9a-fA-F]{40,64}", sha) is None
    ):
        raise RequestError(f"遠端分支 {remote}/{branch} 的 SHA 格式無效")
    return sha.lower()


def require_remote_sha(
    repo: Path, remote: str, branch: str, expected: str, stage: str
) -> None:
    actual = remote_sha(repo, remote, branch)
    if actual != expected:
        raise RequestError(
            f"{stage}的遠端 {remote}/{branch} 已移動：預期 {expected}，實際 {actual or '(不存在)'}"
        )


def base_context(repo: Path) -> BaseContext:
    status_text = git(repo, "status", "--porcelain=v1", "--untracked-files=all")
    if status_text:
        preview = "；".join(status_text.splitlines()[:5])
        raise RequestError(f"建立工作樹前，主工作樹必須沒有未提交變更：{preview}")
    branch = optional_git(repo, "symbolic-ref", "--quiet", "--short", "HEAD")
    if branch != TRUSTED_BASE_BRANCH:
        raise RequestError(
            f"必須從受信任基準分支 {TRUSTED_BASE_BRANCH} 啟動，目前分支為 {branch or '(detached HEAD)'}"
        )
    head_sha = git(repo, "rev-parse", "HEAD").lower()
    remote = optional_git(repo, "config", "--get", f"branch.{branch}.remote")
    merge_ref = optional_git(repo, "config", "--get", f"branch.{branch}.merge")
    if (
        not remote
        or remote == "."
        or not merge_ref
        or not merge_ref.startswith("refs/heads/")
    ):
        raise RequestError(f"目前分支 {branch} 必須追蹤 GitHub 遠端分支")
    remote_branch = merge_ref.removeprefix("refs/heads/")
    if remote_branch != TRUSTED_BASE_BRANCH:
        raise RequestError(f"基準分支必須追蹤遠端 {TRUSTED_BASE_BRANCH}")
    upstream_sha = git(repo, "rev-parse", "@{upstream}").lower()
    if head_sha != upstream_sha:
        raise RequestError(f"目前 HEAD 與本機追蹤分支 {remote}/{remote_branch} 不一致")
    remote_url = git(repo, "remote", "get-url", "--push", remote)
    host, repository = github_remote(remote_url)
    require_remote_sha(repo, remote, remote_branch, head_sha, "建立工作前")
    if not shutil_which("gh"):
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


def shutil_which(command: str) -> str | None:
    """保留單一可替換點，方便工具測試隔離外部指令。"""
    from shutil import which

    return which(command)


def assignment_digest(session_id: str, target_class: str, base_sha: str) -> str:
    value = f"{ASSIGNMENT_VERSION}\0{session_id}\0{target_class}\0{base_sha}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def branch_name(session_id: str, target_class: str, base_sha: str) -> str:
    simple_name = target_class.rsplit(".", 1)[-1]
    slug = re.sub(r"[^a-z0-9]+", "-", simple_name.lower()).strip("-") or "java-test"
    digest = assignment_digest(session_id, target_class, base_sha)
    return f"{BRANCH_PREFIX}/{slug[:48]}-{digest[:12]}"


def target_source_path(target_class: str) -> PurePosixPath:
    return PurePosixPath("src", "main", "java", *target_class.split(".")).with_suffix(
        ".java"
    )


def candidate_path(target_class: str) -> PurePosixPath:
    package, _, simple_name = target_class.rpartition(".")
    return PurePosixPath(
        "src", "test", "java", *package.split("."), f"{simple_name}Test.java"
    )


def validate_target(repo: Path, target_class: str) -> dict[str, str]:
    if not JAVA_CLASS.fullmatch(target_class):
        raise RequestError(f"完整類別名稱格式無效：{target_class}")
    simple_name = target_class.rsplit(".", 1)[-1]
    if not simple_name.endswith("Service"):
        raise RequestError(
            f"派工目標必須是以 Service 結尾的完整類別名稱：{target_class}"
        )
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
        raise RequestError(
            f"正式 Service 的 package 與完整類別名稱不一致：{target_class}"
        )
    return {
        "target_class": target_class,
        "target_source": source_relative.as_posix(),
        "candidate_class": f"{target_class}Test",
        "test_file": candidate_path(target_class).as_posix(),
    }


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
    if any(CASE_ID.fullmatch(case_id) is None for case_id in ids):
        raise RequestError("測試案例編號必須使用 UT-001 格式")
    if len(ids) != len(set(ids)):
        raise RequestError("測試案例編號不得重複")
    return cases


def git_nul_paths(repo: Path, *arguments: str) -> set[str]:
    result = run_command(
        ["git", "-C", str(repo), *arguments],
        cwd=repo,
        timeout=GIT_TIMEOUT_SECONDS,
    )
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


def assignment_state_path(repo: Path, assignment_id: str) -> Path:
    if ASSIGNMENT_ID.fullmatch(assignment_id) is None:
        raise RequestError("派工識別碼格式無效")
    return state_directory(repo) / f"{assignment_id}.json"


def load_assignment(
    repo: Path,
    assignment_id: str,
    session_id: str,
    *,
    bind_worker: bool,
) -> Assignment:
    path = assignment_state_path(repo, assignment_id)
    if path.is_symlink() or not path.is_file():
        raise RequestError("找不到派工狀態；請確認使用 prepare 回傳的派工識別碼")
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise RequestError("派工狀態檔權限過寬")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeError) as exc:
        raise RequestError("無法讀取派工狀態") from exc
    if not isinstance(data, dict) or data.get("version") != ASSIGNMENT_VERSION:
        raise RequestError("派工狀態版本無效")
    if parse_required_string(data, "assignment_id") != assignment_id:
        raise RequestError("派工狀態與派工識別碼不一致")
    coordinator_repo = Path(parse_required_string(data, "coordinator_repo")).resolve()
    if coordinator_repo != repo.resolve():
        raise RequestError("派工狀態不屬於目前專案")

    worker_session_id = data.get("worker_session_id")
    if worker_session_id is None and bind_worker:
        data["worker_session_id"] = session_id
        atomic_write_json(path, data)
        worker_session_id = session_id
    elif worker_session_id != session_id:
        raise RequestError("目前子代理工作階段與派工狀態不一致")

    base_data = data.get("base")
    if not isinstance(base_data, dict):
        raise RequestError("派工狀態缺少 Git 基準")
    base = BaseContext(
        branch=parse_required_string(base_data, "branch"),
        head_sha=parse_required_string(base_data, "head_sha").lower(),
        remote=parse_required_string(base_data, "remote"),
        remote_branch=parse_required_string(base_data, "remote_branch"),
        github_host=parse_required_string(base_data, "github_host"),
        github_repository=parse_required_string(base_data, "github_repository"),
    )
    target_class = parse_required_string(data, "target_class")
    target = validate_target(repo, target_class)
    for key in ("target_source", "candidate_class", "test_file"):
        if parse_required_string(data, key) != target[key]:
            raise RequestError(f"派工狀態的 {key} 與目標類別不一致")
    expected_id = assignment_digest(
        parse_required_string(data, "coordinator_session_id"),
        target_class,
        base.head_sha,
    )[:24]
    if assignment_id != expected_id:
        raise RequestError("派工識別碼與工作內容不一致")
    branch = parse_required_string(data, "branch")
    if branch != branch_name(
        data["coordinator_session_id"], target_class, base.head_sha
    ):
        raise RequestError("派工分支名稱與工作內容不一致")
    worktree = Path(parse_required_string(data, "worktree")).resolve()
    allowed_root = (repo / "unit-test-worktrees").resolve()
    if not worktree.is_relative_to(allowed_root) or worktree == allowed_root:
        raise RequestError("派工工作樹不在 unit-test-worktrees 內")
    raw_sources = data.get("specification_sources")
    if (
        not isinstance(raw_sources, list)
        or not raw_sources
        or any(not isinstance(item, str) for item in raw_sources)
    ):
        raise RequestError("派工狀態缺少可信規格來源")
    assignment = Assignment(
        assignment_id=assignment_id,
        coordinator_session_id=data["coordinator_session_id"],
        worker_session_id=worker_session_id,
        coordinator_repo=repo.resolve(),
        state_path=path,
        worktree=worktree,
        branch=branch,
        target_class=target_class,
        target_source=target["target_source"],
        candidate_class=target["candidate_class"],
        test_file=target["test_file"],
        specification_sources=tuple(raw_sources),
        base=base,
        state=data,
    )
    verify_assignment_state(assignment)
    return assignment


def verify_assignment_state(assignment: Assignment) -> None:
    worktree = assignment.worktree
    if not worktree.is_dir() or worktree.is_symlink():
        raise RequestError("派工工作樹不存在或不是一般目錄")
    top = git(
        worktree, "rev-parse", "--show-toplevel", message="派工路徑不是 Git worktree"
    )
    if Path(top).resolve() != worktree:
        raise RequestError("派工路徑不是 Git worktree 根目錄")
    branch = optional_git(worktree, "symbolic-ref", "--quiet", "--short", "HEAD")
    if branch != assignment.branch:
        raise RequestError(
            f"目前 worktree 分支不是派工分支：預期 {assignment.branch}，實際 {branch or '(detached HEAD)'}"
        )
    if git_common_dir(worktree) != git_common_dir(assignment.coordinator_repo):
        raise RequestError("派工工作樹不屬於目前 Git repository")
    if git(worktree, "rev-parse", "HEAD").lower() != assignment.base.head_sha:
        raise RequestError("派工分支已有未經發布工具建立的提交")
    remote_url = git(worktree, "remote", "get-url", "--push", assignment.base.remote)
    if github_remote(remote_url) != (
        assignment.base.github_host,
        assignment.base.github_repository,
    ):
        raise RequestError("Git push remote 與派工狀態不一致")
    require_remote_sha(
        worktree,
        assignment.base.remote,
        assignment.base.remote_branch,
        assignment.base.head_sha,
        "工具執行時",
    )


def candidate_snapshot(
    assignment: Assignment,
    data: dict[str, Any],
    *,
    require_cases: bool,
) -> dict[str, Any]:
    cases = validate_test_cases(data) if require_cases else []
    require_only_path(
        changed_paths(assignment.worktree), assignment.test_file, "檢查候選測試時"
    )
    path = destination(assignment.worktree, PurePosixPath(assignment.test_file))
    if path.is_symlink() or not path.is_file():
        raise RequestError(f"子代理必須建立唯一測試檔：{assignment.test_file}")
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise RequestError("無法讀取候選測試檔") from exc
    if not content.strip():
        raise RequestError("候選測試內容不得為空")
    if len(content.encode("utf-8")) > MAX_FILE_BYTES:
        raise RequestError(f"候選測試不得超過 {MAX_FILE_BYTES} bytes")
    expected_package = assignment.target_class.rpartition(".")[0]
    package_match = PACKAGE.search(content)
    if package_match is None or package_match.group(1) != expected_package:
        raise RequestError(f"候選測試 package 應為 {expected_package}")
    simple_name = assignment.candidate_class.rsplit(".", 1)[-1]
    if re.search(rf"\bclass\s+{re.escape(simple_name)}\b", content) is None:
        raise RequestError(f"候選內容缺少類別 {simple_name}")
    missing_ids = [case["id"] for case in cases if case["id"] not in content]
    if missing_ids:
        raise RequestError("候選測試缺少案例編號：" + ", ".join(missing_ids))
    return {
        "content": content,
        "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "test_cases": cases,
    }


def save_assignment_state(assignment: Assignment, state: dict[str, Any]) -> None:
    atomic_write_json(assignment.state_path, state)


def result_error(exc: Exception, *, action: str) -> dict[str, Any]:
    return {
        "status": "cancelled" if _CANCEL_REQUESTED else f"{action}-failed",
        "message": str(exc),
        "submitted": False,
        "pr_created": False,
        "merged": False,
    }
