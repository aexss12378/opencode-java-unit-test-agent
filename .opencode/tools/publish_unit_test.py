"""發布最新驗證通過的候選測試：提交、推送並建立 Draft PR。"""

from __future__ import annotations

import argparse
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

GIT_TIMEOUT_SECONDS = 120
GITHUB_TIMEOUT_SECONDS = 120
MAX_FILE_BYTES = 100_000

ASSIGNMENT_ID = re.compile(r"^[0-9a-f]{24}$")
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


def target_source_path(target_class: str) -> PurePosixPath:
    return PurePosixPath("src", "main", "java", *target_class.split(".")).with_suffix(
        ".java"
    )


def candidate_path(target_class: str) -> PurePosixPath:
    package, _, simple_name = target_class.rpartition(".")
    return PurePosixPath(
        "src", "test", "java", *package.split("."), f"{simple_name}Test.java"
    )


def validate_target(_repo: Path, target_class: str) -> dict[str, str]:
    if not JAVA_CLASS.fullmatch(target_class):
        raise RequestError(f"完整類別名稱格式無效：{target_class}")
    source_relative = target_source_path(target_class)
    return {
        "target_class": target_class,
        "target_source": source_relative.as_posix(),
        "candidate_class": f"{target_class}Test",
        "test_file": candidate_path(target_class).as_posix(),
    }


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
    if not isinstance(data, dict):
        raise RequestError("派工狀態格式無效")
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
    branch = parse_required_string(data, "branch")
    worktree = Path(parse_required_string(data, "worktree")).resolve()
    allowed_root = (repo / "unit-test-worktrees").resolve()
    if not worktree.is_relative_to(allowed_root) or worktree == allowed_root:
        raise RequestError("派工工作樹不在 unit-test-worktrees 內")
    raw_sources = data.get("specification_sources")
    if not isinstance(raw_sources, list) or any(
        not isinstance(item, str) for item in raw_sources
    ):
        raise RequestError("派工狀態的外部規格格式無效")
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


def candidate_snapshot(assignment: Assignment) -> dict[str, Any]:
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
    return {
        "content": content,
        "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "test_cases": [],
    }


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
    snapshot = candidate_snapshot(assignment)
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
