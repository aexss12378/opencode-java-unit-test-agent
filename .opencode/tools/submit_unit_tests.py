"""在無 .git 的短暫副本驗證 Java 候選測試，通過後建立 Draft PR。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
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
MAVEN_TIMEOUT_SECONDS = 600
GIT_TIMEOUT_SECONDS = 120
GITHUB_TIMEOUT_SECONDS = 120
MINIMUM_LINE_COVERAGE_PERCENT = 80
BRANCH_PREFIX = "opencode/unit-test"
TRUSTED_BASE_BRANCH = "main"
CASE_ID = re.compile(r"^UT-[0-9]{3,}$")
SESSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,200}$")
PACKAGE = re.compile(
    r"^\s*package\s+([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)\s*;",
    re.MULTILINE,
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
        raise RequestError(f"{message}" + (f"：{detail}" if detail else ""))
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


# INPUT


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


def destination(root: Path, relative: PurePosixPath) -> Path:
    target = root.joinpath(*relative.parts)
    if not target.resolve(strict=False).is_relative_to(root.resolve()):
        raise RequestError(f"路徑離開專案範圍：{relative}")
    return target


def validate_request(repo: Path) -> dict[str, Any]:
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        raise RequestError(f"輸入不是有效 JSON：{exc}") from exc
    if not isinstance(data, dict):
        raise RequestError("輸入必須是 JSON 物件")

    raw_cases = data.get("test_cases")
    if not isinstance(raw_cases, list) or not 1 <= len(raw_cases) <= 50:
        raise RequestError("test_cases 數量必須介於 1 到 50")
    cases: list[dict[str, str]] = []
    for raw in raw_cases:
        if not isinstance(raw, dict):
            raise RequestError("每個測試案例都必須是物件")
        case = {}
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

    raw_file = data.get("file")
    if not isinstance(raw_file, dict):
        raise RequestError("file 必須是物件")
    raw_path = raw_file.get("path")
    if not isinstance(raw_path, str) or "\\" in raw_path:
        raise RequestError("測試路徑必須是使用 / 的相對路徑")
    path = PurePosixPath(raw_path)
    if (
        path.is_absolute()
        or path.as_posix() != raw_path
        or any(part in {"", ".", ".."} for part in path.parts)
        or path.parts[:3] != ("src", "test", "java")
    ):
        raise RequestError("測試檔只能位於 src/test/java/**")
    if path.suffix != ".java" or path.stem == "Test" or not path.stem.endswith("Test"):
        raise RequestError("測試檔名必須是受測類別名稱加上 Test.java")
    content = raw_file.get("content")
    if not isinstance(content, str) or not content.strip():
        raise RequestError("候選測試內容不得為空")
    if len(content.encode("utf-8")) > MAX_FILE_BYTES:
        raise RequestError(f"候選測試不得超過 {MAX_FILE_BYTES} bytes")
    missing_ids = [case_id for case_id in ids if case_id not in content]
    if missing_ids:
        raise RequestError("候選測試缺少案例編號：" + ", ".join(missing_ids))

    expected_package = ".".join(path.parts[3:-1])
    package_match = PACKAGE.search(content)
    actual_package = package_match.group(1) if package_match else ""
    if actual_package != expected_package:
        raise RequestError(f"package 應為 {expected_package or '(default package)'}")
    if not re.search(rf"\bclass\s+{re.escape(path.stem)}\b", content):
        raise RequestError(f"候選內容缺少類別 {path.stem}")

    target = destination(repo, path)
    if target.is_symlink() or (target.exists() and not target.is_file()):
        raise RequestError("正式測試路徑不是一般檔案")
    if target.is_file() and target.read_text(encoding="utf-8") == content:
        raise RequestError("候選測試與目前檔案相同")

    candidate_class = f"{actual_package}.{path.stem}" if actual_package else path.stem
    target_simple_name = path.stem.removesuffix("Test")
    target_class = f"{actual_package}.{target_simple_name}" if actual_package else target_simple_name
    target_relative = PurePosixPath("src", "main", "java", *target_class.split(".")).with_suffix(".java")
    target_source = destination(repo, target_relative)
    if target_source.is_symlink() or not target_source.is_file():
        raise RequestError(f"找不到與候選測試對應的正式類別：{target_class}")

    return {
        "target_class": target_class,
        "test_cases": cases,
        "file": {"path": path.as_posix(), "content": content},
        "candidate_class": candidate_class,
    }


# GITHUB


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


def command_failure(result: subprocess.CompletedProcess[str], message: str) -> RequestError:
    detail = (result.stdout + result.stderr).strip()[-4000:]
    return RequestError(message + (f"：{detail}" if detail else ""))


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
        raise RequestError(
            f"{stage}的遠端 {remote}/{branch} 已移動：預期 {expected}，實際 {actual or '(不存在)'}"
        )


def base_context(repo: Path) -> BaseContext:
    status = git(repo, "status", "--porcelain=v1", "--untracked-files=all")
    if status:
        preview = "；".join(status.splitlines()[:5])
        raise RequestError(f"建立子代理分支前，基準 worktree 必須沒有未提交變更：{preview}")

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
        raise RequestError(
            f"基準分支 {branch} 必須追蹤遠端 {TRUSTED_BASE_BRANCH}，目前追蹤 {remote_branch}"
        )
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


# WORKTREE


def branch_name(session_id: str, request: dict[str, Any]) -> str:
    simple_name = request["target_class"].rsplit(".", 1)[-1]
    slug = re.sub(r"[^a-z0-9]+", "-", simple_name.lower()).strip("-") or "java-test"
    digest_input = "\0".join(
        (session_id, request["target_class"], request["file"]["path"], request["file"]["content"])
    )
    digest = hashlib.sha256(digest_input.encode("utf-8")).hexdigest()[:12]
    return f"{BRANCH_PREFIX}/{slug[:48]}-{digest}"


def ensure_branch_available(repo: Path, base: BaseContext, branch: str) -> None:
    local = run_command(
        ["git", "-C", str(repo), "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
        cwd=repo,
        timeout=GIT_TIMEOUT_SECONDS,
    )
    if local.returncode == 0:
        raise BranchConflictError(f"本機分支已存在，需要人工確認先前提交結果：{branch}")
    if local.returncode != 1:
        raise command_failure(local, f"無法檢查本機分支 {branch}")
    if remote_sha(repo, base.remote, branch) is not None:
        raise BranchConflictError(f"遠端分支已存在，需要人工確認先前提交或 PR：{base.remote}/{branch}")


def create_worktree(repo: Path, base: BaseContext, branch: str) -> Worktree:
    ensure_branch_available(repo, base, branch)
    root = Path(tempfile.mkdtemp(prefix="opencode-unit-test-"))
    project = root / "repo"
    result = run_command(
        [
            "git",
            "-C",
            str(repo),
            "worktree",
            "add",
            "--quiet",
            "-b",
            branch,
            str(project),
            base.head_sha,
        ],
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


def create_validation_copy(source: Path, project: Path) -> None:
    source_root = source.resolve()

    def ignored(path: str, names: list[str]) -> set[str]:
        result = {name for name in names if name in {".git", ".opencode"}}
        if Path(path).resolve() == source_root and "target" in names:
            result.add("target")
        return result

    shutil.copytree(
        source,
        project,
        symlinks=True,
        ignore=ignored,
    )
    if (project / ".git").exists():
        raise RequestError("Maven 驗證副本不得包含 .git")


def cleanup_worktree(repo: Path, worktree: Worktree | None, delete_branch: bool) -> list[str]:
    if worktree is None:
        return []
    warnings: list[str] = []
    removed = run_command(
        ["git", "-C", str(repo), "worktree", "remove", "--force", str(worktree.project)],
        cwd=repo,
        timeout=GIT_TIMEOUT_SECONDS,
        allow_cancelled=True,
    )
    if removed.returncode != 0:
        warnings.append(
            f"Git 無法移除暫存 worktree；為避免留下失效的 Git 登錄，已保留於 {worktree.project}，"
            "需要人工執行 git worktree remove --force <path>"
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


# APPLY


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_candidate(root: Path, file: dict[str, str]) -> None:
    atomic_write(destination(root, PurePosixPath(file["path"])), file["content"])


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
        raise RequestError(f"{stage} 的 Git 變更不只包含 {expected}：{shown}")


# VALIDATE


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
    exit_code, timed_out = result.returncode, result.returncode == 124
    return {
        "command": f"./mvnw -B -ntp -Dtest={candidate_class} test",
        "exit_code": exit_code,
        "timed_out": timed_out,
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
    if not report.is_file():
        raise RequestError("Maven test 成功，但找不到 target/site/jacoco/jacoco.xml")
    try:
        root = ET.parse(report).getroot()
    except (ET.ParseError, OSError) as exc:
        raise RequestError("無法解析 target/site/jacoco/jacoco.xml") from exc

    target_name = target_class.replace(".", "/")
    target = next(
        (
            node
            for node in root.iter()
            if node.tag.rsplit("}", 1)[-1] == "class" and node.attrib.get("name") == target_name
        ),
        None,
    )
    if target is None:
        raise RequestError(f"JaCoCo XML 找不到受測正式類別：{target_class}")

    counter = next(
        (
            node
            for node in target
            if node.tag.rsplit("}", 1)[-1] == "counter" and node.attrib.get("type") == "LINE"
        ),
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
    }


def validation_failure(
    status: str,
    message: str,
    validation: dict[str, Any],
    **extra: Any,
) -> dict[str, Any]:
    return {
        "status": status,
        "message": message,
        "submitted": False,
        "pr_created": False,
        "merged": False,
        "validation": validation,
        **extra,
    }


def validate_candidate(project: Path, request: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    expected_path = request["file"]["path"]
    write_candidate(project, request["file"])

    maven = run_maven(project, request["candidate_class"])
    validation = {key: maven[key] for key in ("command", "exit_code", "timed_out")}
    if maven["exit_code"] == 130 and _CANCEL_REQUESTED:
        return None, validation_failure(
            "cancelled",
            "單元測試工作已取消；沒有推送分支或建立 PR。",
            validation,
        )
    if maven["exit_code"] != 0:
        return None, validation_failure(
            "candidate-check-failed",
            (
                "候選測試未通過本機 Maven test；請依規格證據判斷是候選測試錯誤，"
                "或可能的正式原始碼缺陷。不得為了讓測試通過而改寫有證據支持的預期結果。"
            ),
            validation,
            diagnostic_field="maven_errors",
            agent_action="若為候選測試的編譯、匯入或設定錯誤，修正候選內容後重新提交。",
            maven_errors=maven["maven_errors"],
        )

    summary = test_summary(project, request["candidate_class"])
    validation["candidate_tests"] = summary
    if summary["tests"] == 0 or summary["skipped"]:
        return None, validation_failure(
            "candidate-not-executed",
            "Maven 成功，但候選測試沒有全部實際執行。",
            validation,
        )
    if summary["unexpected_classes"]:
        return None, validation_failure(
            "candidate-not-isolated",
            "Maven 執行了候選類別以外的測試，無法單獨計算候選測試覆蓋率。",
            validation,
        )

    try:
        coverage = coverage_summary(project, request["target_class"])
    except RequestError as exc:
        return None, validation_failure("coverage-report-invalid", str(exc), validation)
    validation["coverage"] = coverage
    if not coverage["passed"]:
        return None, validation_failure(
            "coverage-below-threshold",
            (
                f"候選測試對 {request['target_class']} 的行覆蓋率為 {coverage['percent']:.2f}%，"
                f"低於 {coverage['minimum_percent']}% 門檻。"
            ),
            validation,
        )

    if destination(project, PurePosixPath(expected_path)).read_text(encoding="utf-8") != request["file"]["content"]:
        raise RequestError("Maven 驗證後的候選測試內容與提交內容不一致")
    return validation, None


# SUBMIT


def commit_candidate(project: Path, base: BaseContext, request: dict[str, Any]) -> tuple[str, str]:
    path = request["file"]["path"]
    content = request["file"]["content"]
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    git(project, "add", "--", path, message="無法暫存候選測試")
    require_only_path(
        git_nul_paths(project, "diff", "--cached", "--name-only", "-z", "--"),
        path,
        "建立提交前",
    )
    if git_nul_paths(project, "diff", "--name-only", "-z", "--") or git_nul_paths(
        project, "ls-files", "--others", "--exclude-standard", "-z"
    ):
        raise RequestError("建立提交前仍有未暫存或未追蹤的額外變更")
    git(project, "diff", "--cached", "--check", "--", message="候選測試未通過 git diff --check")

    title = f"新增 {request['target_class']} 單元測試"
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
    if git(project, "rev-parse", "HEAD^").lower() != base.head_sha:
        raise RequestError("候選測試提交的父提交不是已驗證的 base SHA")
    require_only_path(
        git_nul_paths(project, "diff-tree", "--no-commit-id", "--name-only", "-r", "-z", "HEAD"),
        path,
        "候選測試提交",
    )
    shown = run_command(
        ["git", "-C", str(project), "show", f"{head_sha}:{path}"],
        cwd=project,
        timeout=GIT_TIMEOUT_SECONDS,
    )
    if shown.returncode != 0:
        raise command_failure(shown, "無法讀取候選測試提交內容")
    if hashlib.sha256(shown.stdout.encode("utf-8")).hexdigest() != digest:
        raise RequestError("候選測試提交內容的 SHA-256 與本機驗證內容不一致")
    if changed_paths(project):
        raise RequestError("建立提交後 worktree 仍有額外變更")
    return head_sha, digest


def push_branch(project: Path, base: BaseContext, branch: str) -> None:
    result = run_command(
        [
            "git",
            "-C",
            str(project),
            "push",
            "--porcelain",
            base.remote,
            f"HEAD:refs/heads/{branch}",
        ],
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
    base: BaseContext,
    branch: str,
    head_sha: str,
    candidate_digest: str,
) -> str:
    cases = []
    for case in request["test_cases"]:
        cases.append(
            f"### {case['id']}\n\n"
            f"- 情境：{case['scenario']}\n"
            f"- 預期：{case['expected']}\n"
            f"- 規格依據：{case['evidence']}"
        )
    body = (
        "## 單元測試候選\n\n"
        f"- 受測類別：`{request['target_class']}`\n"
        f"- 測試檔：`{request['file']['path']}`\n"
        f"- 基準：`{base.remote_branch}` (`{base.head_sha}`)\n"
        f"- 分支：`{branch}`\n"
        f"- 提交：`{head_sha}`\n"
        f"- 候選內容 SHA-256：`{candidate_digest}`\n\n"
        "## 本機驗證\n\n"
        f"- 指令：`{validation['command']}`\n"
        f"- 實際執行測試：{validation['candidate_tests']['executed']}\n"
        f"- 目標類別行覆蓋率：{validation['coverage']['percent']:.2f}%"
        f"（門檻 {validation['coverage']['minimum_percent']}%）\n\n"
        "## 測試案例與依據\n\n"
        + "\n\n".join(cases)
        + "\n\n---\n\n"
        "此 PR 必須由工程師審查。自動化工具不會將它轉為 Ready，也不會合併。\n"
    )
    if len(body.encode("utf-8")) > MAX_PR_BODY_BYTES:
        raise RequestError(f"Draft PR 內容超過 {MAX_PR_BODY_BYTES} bytes，請縮短案例描述或規格依據")
    return body


def create_draft_pr(
    project: Path,
    request: dict[str, Any],
    validation: dict[str, Any],
    base: BaseContext,
    branch: str,
    head_sha: str,
    candidate_digest: str,
) -> dict[str, Any]:
    body = pr_body(request, validation, base, branch, head_sha, candidate_digest)
    locator = github_locator(base)
    title = f"test: 新增 {request['target_class']} 單元測試"
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
                branch,
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
        failure = command_failure(created, f"分支已推送，但 gh 未能確認 Draft PR 結果：{branch}")
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
        "headRefName": branch,
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


def submit(repo: Path, session_id: str, request: dict[str, Any]) -> dict[str, Any]:
    try:
        base = base_context(repo)
    except RequestError as exc:
        return {
            "status": "cancelled" if _CANCEL_REQUESTED else "preflight-failed",
            "message": str(exc),
            "submitted": False,
            "pr_created": False,
            "merged": False,
        }

    branch = branch_name(session_id, request)
    worktree: Worktree | None = None
    committed = False
    pushed = False
    head_sha: str | None = None
    validation: dict[str, Any] | None = None
    result: dict[str, Any]
    try:
        worktree = create_worktree(repo, base, branch)
        with tempfile.TemporaryDirectory(prefix="opencode-unit-test-validation-") as temporary:
            validation_project = Path(temporary) / "project"
            create_validation_copy(worktree.project, validation_project)
            validation, failure = validate_candidate(validation_project, request)
        if failure is not None:
            result = {**failure, "branch": branch, "base_sha": base.head_sha}
        else:
            assert validation is not None
            write_candidate(worktree.project, request["file"])
            require_only_path(
                changed_paths(worktree.project),
                request["file"]["path"],
                "寫入已驗證候選測試後",
            )
            require_remote_sha(
                worktree.project,
                base.remote,
                base.remote_branch,
                base.head_sha,
                "Maven 驗證後",
            )
            head_sha, digest = commit_candidate(worktree.project, base, request)
            committed = True
            pr_body(request, validation, base, branch, head_sha, digest)
            try:
                push_branch(worktree.project, base, branch)
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
                        "branch": branch,
                        "base_sha": base.head_sha,
                        "commit_sha": head_sha,
                        "validation": validation,
                    }
                else:
                    observed_remote_sha: str | None = None
                    remote_state = "unknown-after-push-attempt"
                    try:
                        observed_remote_sha = remote_sha(worktree.project, base.remote, branch)
                        remote_state = "verified-after-push-attempt"
                    except RequestError:
                        pass
                    result = {
                        "status": "push-failed",
                        "message": str(exc),
                        "submitted": (
                            observed_remote_sha == head_sha if remote_state == "verified-after-push-attempt" else None
                        ),
                        "remote_sha": observed_remote_sha,
                        "remote_state": remote_state,
                        "pr_created": False,
                        "merged": False,
                        "manual_recovery_required": True,
                        "automatic_retry_supported": False,
                        "branch": branch,
                        "base_sha": base.head_sha,
                        "commit_sha": head_sha,
                        "validation": validation,
                    }
                    if observed_remote_sha == head_sha:
                        result["compare_url"] = compare_url(base, branch)
            else:
                pushed = True
                live_sha = remote_sha(worktree.project, base.remote, branch)
                if live_sha != head_sha:
                    raise RequestError(
                        f"推送後遠端分支 SHA 不一致：預期 {head_sha}，實際 {live_sha or '(不存在)'}"
                    )
                require_remote_sha(
                    worktree.project,
                    base.remote,
                    base.remote_branch,
                    base.head_sha,
                    "建立 PR 前",
                )
                pr: dict[str, Any] | None = None
                verified_sha: str | None = None
                try:
                    pr = create_draft_pr(
                        worktree.project,
                        request,
                        validation,
                        base,
                        branch,
                        head_sha,
                        digest,
                    )
                    verified_sha = remote_sha(worktree.project, base.remote, branch)
                    if verified_sha != head_sha:
                        raise RequestError(
                            f"PR 建立後遠端分支 SHA 不一致：預期 {head_sha}，實際 {verified_sha or '(不存在)'}"
                        )
                    require_remote_sha(
                        worktree.project,
                        base.remote,
                        base.remote_branch,
                        base.head_sha,
                        "PR 建立後",
                    )
                except RequestError as exc:
                    pr_url = pr["url"] if pr is not None else getattr(exc, "pr_url", None)
                    cancelled = _CANCEL_REQUESTED
                    pr_state_unknown = cancelled or isinstance(exc, DraftPrStateUnknownError)
                    result = {
                        "status": "cancelled" if cancelled else "pr-create-or-verify-failed",
                        "message": (
                            "工作在建立或驗證 PR 期間取消；PR 狀態尚未確認。" if cancelled else str(exc)
                        ),
                        "submitted": True,
                        "pr_created": None if pr_state_unknown and pr_url is None else pr_url is not None,
                        "pr_verified": False,
                        "merged": False,
                        "manual_recovery_required": True,
                        "automatic_retry_supported": False,
                        "branch": branch,
                        "base_sha": base.head_sha,
                        "commit_sha": head_sha,
                        "remote_sha": verified_sha,
                        "compare_url": compare_url(base, branch),
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
                        "message": "候選測試已通過本機 Maven 驗證，並建立等待人工審查的 Draft PR。",
                        "submitted": True,
                        "pr_created": True,
                        "pr_verified": True,
                        "merged": False,
                        "target_class": request["target_class"],
                        "test_file": request["file"]["path"],
                        "base_branch": base.remote_branch,
                        "base_sha": base.head_sha,
                        "branch": branch,
                        "commit_sha": head_sha,
                        "remote_sha": verified_sha,
                        "pr": {
                            "number": pr["number"],
                            "url": pr["url"],
                            "draft": pr["isDraft"],
                        },
                        "validation": validation,
                    }
    except (RequestError, OSError, UnicodeError) as exc:
        branch_conflict = isinstance(exc, BranchConflictError)
        result = {
            "status": (
                "cancelled" if _CANCEL_REQUESTED else "branch-conflict" if branch_conflict else "submission-failed"
            ),
            "message": str(exc),
            "submitted": pushed,
            "pr_created": False,
            "merged": False,
            "branch": branch,
            "base_sha": base.head_sha,
            "manual_recovery_required": committed or branch_conflict,
            "automatic_retry_supported": not (committed or branch_conflict),
        }
        if head_sha is not None:
            result["commit_sha"] = head_sha
        if validation is not None:
            result["validation"] = validation
        if pushed:
            result["compare_url"] = compare_url(base, branch)
    warnings = cleanup_worktree(repo, worktree, delete_branch=not (committed and not pushed))
    if warnings:
        result["cleanup_warnings"] = warnings
    return result


# CLI


def main() -> int:
    install_signal_handlers()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("submit",))
    parser.add_argument("--repo", default=".")
    parser.add_argument("--session-id", required=True)
    args = parser.parse_args()
    try:
        repo = repo_root(args.repo)
        session_id = validate_session_id(args.session_id)
        request = validate_request(repo)
        result = submit(repo, session_id, request)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["status"] == "draft-pr-created" else 3
    except (RequestError, OSError, UnicodeError) as exc:
        result = {
            "status": "invalid-request",
            "message": str(exc),
            "submitted": False,
            "pr_created": False,
            "merged": False,
        }
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
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
