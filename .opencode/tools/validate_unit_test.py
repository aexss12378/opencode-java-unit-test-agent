"""在指定 Java 型別的獨立工作樹中驗證唯一候選測試。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import shutil
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

GIT_TIMEOUT_SECONDS = 120
MAX_FILE_BYTES = 100_000
MAVEN_TIMEOUT_SECONDS = 600
MINIMUM_LINE_COVERAGE_PERCENT = 80

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


def validation_pom(project: Path, candidate_class: str) -> Path:
    source = project / "pom.xml"
    try:
        tree = ET.parse(source)
    except (ET.ParseError, OSError) as exc:
        raise RequestError("無法解析 pom.xml 以隔離候選測試編譯") from exc

    root = tree.getroot()
    namespace = ""
    if root.tag.startswith("{"):
        namespace = root.tag.partition("}")[0] + "}"
        ET.register_namespace("", namespace[1:-1])

    def tag(name: str) -> str:
        return f"{namespace}{name}"

    build = root.find(tag("build"))
    if build is None:
        build = ET.SubElement(root, tag("build"))
    plugins = build.find(tag("plugins"))
    if plugins is None:
        plugins = ET.SubElement(build, tag("plugins"))

    compiler = None
    for plugin in plugins.findall(tag("plugin")):
        artifact = plugin.find(tag("artifactId"))
        group = plugin.find(tag("groupId"))
        if (
            artifact is not None
            and artifact.text == "maven-compiler-plugin"
            and (group is None or group.text in (None, "org.apache.maven.plugins"))
        ):
            compiler = plugin
            break
    if compiler is None:
        compiler = ET.SubElement(plugins, tag("plugin"))
        ET.SubElement(compiler, tag("groupId")).text = "org.apache.maven.plugins"
        ET.SubElement(compiler, tag("artifactId")).text = "maven-compiler-plugin"

    configuration = compiler.find(tag("configuration"))
    if configuration is None:
        configuration = ET.SubElement(compiler, tag("configuration"))
    for existing in configuration.findall(tag("testIncludes")):
        configuration.remove(existing)
    includes = ET.SubElement(configuration, tag("testIncludes"))
    ET.SubElement(includes, tag("testInclude")).text = (
        candidate_class.replace(".", "/") + ".java"
    )

    descriptor, raw_path = tempfile.mkstemp(
        prefix=".opencode-validation-", suffix=".xml", dir=project
    )
    os.close(descriptor)
    path = Path(raw_path)
    try:
        tree.write(path, encoding="utf-8", xml_declaration=True)
    except (OSError, UnicodeError) as exc:
        path.unlink(missing_ok=True)
        raise RequestError("無法建立候選測試的 Maven 隔離設定") from exc
    return path


def clear_maven_outputs(project: Path) -> None:
    target = project / "target"
    if target.is_symlink():
        raise RequestError("target 不得是符號連結")
    if target.exists() and not target.is_dir():
        raise RequestError("target 不是目錄")
    if target.is_dir():
        shutil.rmtree(target)


def maven_environment(project: Path) -> dict[str, str]:
    allowed = {
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
    environment = {name: value for name, value in os.environ.items() if name in allowed}
    isolated = project.parent / ".opencode-validation-config"
    (isolated / "gh").mkdir(mode=0o700, parents=True, exist_ok=True)
    environment.update(
        {
            "CI": "true",
            "TERM": "dumb",
            "PWD": str(project),
            "GH_CONFIG_DIR": str(isolated / "gh"),
            "GH_PROMPT_DISABLED": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    return environment


def run_maven(project: Path, candidate_class: str) -> dict[str, Any]:
    pom = validation_pom(project, candidate_class)
    try:
        result = run_command(
            [
                str(project / "mvnw"),
                "-B",
                "-ntp",
                "-f",
                str(pom),
                f"-Dtest={candidate_class}",
                "test",
            ],
            cwd=project,
            timeout=MAVEN_TIMEOUT_SECONDS,
            env=maven_environment(project),
        )
    finally:
        pom.unlink(missing_ok=True)
    output_lines = result.stdout.splitlines() + result.stderr.splitlines()
    return {
        "command": (
            "./mvnw -B -ntp -f <isolated-test-pom> "
            f"-Dtest={candidate_class} test"
        ),
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
            if class_name != candidate_class and not class_name.startswith(
                candidate_class + "$"
            ):
                if class_name:
                    unexpected_classes.add(class_name)
                continue
            matched = True
            tests += 1
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
    report = project / "target/site/jacoco/jacoco.xml"
    execution_data = project / "target/jacoco.exec"
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
        (
            node
            for node in root.iter()
            if node.tag.rsplit("}", 1)[-1] == "class"
            and node.attrib.get("name") == target_name
        ),
        None,
    )
    if target is None:
        raise RequestError(f"JaCoCo XML 找不到受測正式類別：{target_class}")
    counter = next(
        (
            node
            for node in target
            if node.tag.rsplit("}", 1)[-1] == "counter"
            and node.attrib.get("type") == "LINE"
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
            if package.tag.rsplit("}", 1)[-1] == "package"
            and package.attrib.get("name") == package_name
            for child in package
            if child.tag.rsplit("}", 1)[-1] == "sourcefile"
            and child.attrib.get("name") == source_name
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


def failure(
    status: str,
    message: str,
    assignment: Assignment,
    validation: dict[str, Any] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    result = {
        "status": status,
        "message": message,
        "assignment_id": assignment.assignment_id,
        "target_class": assignment.target_class,
        "test_file": assignment.test_file,
        "worktree": str(assignment.worktree.relative_to(assignment.coordinator_repo)),
        "branch": assignment.branch,
        "base_sha": assignment.base.head_sha,
        "submitted": False,
        "pr_created": False,
        "merged": False,
        **extra,
    }
    if validation is not None:
        result["validation"] = validation
    return result


def validate(assignment: Assignment, data: dict[str, Any]) -> dict[str, Any]:
    verify_assignment_state(assignment)
    snapshot = candidate_snapshot(assignment, data, require_cases=True)
    clear_maven_outputs(assignment.worktree)
    maven = run_maven(assignment.worktree, assignment.candidate_class)
    require_only_path(
        changed_paths(assignment.worktree), assignment.test_file, "Maven 驗證後"
    )
    current = destination(assignment.worktree, PurePosixPath(assignment.test_file))
    if current.is_symlink() or not current.is_file():
        raise RequestError("Maven 驗證後候選測試不再是一般檔案")
    current_digest = hashlib.sha256(current.read_bytes()).hexdigest()
    if current_digest != snapshot["sha256"]:
        raise RequestError("Maven 驗證後的候選測試內容與驗證前不一致")
    validation: dict[str, Any] = {
        key: maven[key] for key in ("command", "exit_code", "timed_out")
    }
    if maven["exit_code"] == 130 and cancelled():
        return failure("cancelled", "單元測試工作已取消。", assignment, validation)
    if maven["exit_code"] != 0:
        return failure(
            "candidate-check-failed",
            "候選測試未通過 Maven test；請修正測試編譯或設定錯誤。可信規格與實作衝突時，不得修改預期結果迎合實作。",
            assignment,
            validation,
            diagnostic_field="maven_errors",
            maven_errors=maven["maven_errors"],
        )
    summary = test_summary(assignment.worktree, assignment.candidate_class)
    validation["candidate_tests"] = summary
    if summary["tests"] == 0 or summary["skipped"]:
        return failure(
            "candidate-not-executed",
            "Maven 成功，但候選測試沒有全部實際執行。",
            assignment,
            validation,
        )
    if summary["unexpected_classes"]:
        return failure(
            "candidate-not-isolated",
            "Maven 執行了候選類別以外的測試。",
            assignment,
            validation,
        )
    try:
        coverage = coverage_summary(assignment.worktree, assignment.target_class)
    except RequestError as exc:
        return failure("coverage-report-invalid", str(exc), assignment, validation)
    validation["coverage"] = coverage
    if not coverage["passed"]:
        return failure(
            "coverage-below-threshold",
            f"目標類別行覆蓋率為 {coverage['percent']:.2f}%，低於 {coverage['minimum_percent']}% 門檻。",
            assignment,
            validation,
        )

    receipt_source = json.dumps(
        {
            "assignment_id": assignment.assignment_id,
            "candidate_sha256": snapshot["sha256"],
            "test_cases": snapshot["test_cases"],
            "validation": validation,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    validation_id = hashlib.sha256(receipt_source.encode("utf-8")).hexdigest()[:24]
    receipt = {
        "validation_id": validation_id,
        "candidate_sha256": snapshot["sha256"],
        "test_cases": snapshot["test_cases"],
        "result": validation,
    }
    state = dict(assignment.state)
    state["status"] = "validated"
    state["validation"] = receipt
    state["publication"] = None
    save_assignment_state(assignment, state)
    return {
        "status": "validation-passed",
        "message": "候選測試已通過 Maven、Surefire 與 JaCoCo 驗證；尚未提交或發布。",
        "assignment_id": assignment.assignment_id,
        "validation_id": validation_id,
        "target_class": assignment.target_class,
        "test_file": assignment.test_file,
        "worktree": str(assignment.worktree.relative_to(assignment.coordinator_repo)),
        "branch": assignment.branch,
        "base_sha": assignment.base.head_sha,
        "candidate_sha256": snapshot["sha256"],
        "submitted": False,
        "pr_created": False,
        "merged": False,
        "validation": validation,
    }


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
        assignment = load_assignment(repo, assignment_id, session_id, bind_worker=True)
        result = validate(assignment, data)
        successful = result["status"] == "validation-passed"
    except (RequestError, OSError, UnicodeError) as exc:
        result = {
            "status": "cancelled" if cancelled() else "validation-failed",
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
