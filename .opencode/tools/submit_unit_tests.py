#!/usr/bin/env python3
"""在隔離副本驗證已核准的 JUnit 候選測試，通過後才發布新測試檔。"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import traceback
import xml.etree.ElementTree as ET
from pathlib import Path, PurePosixPath
from typing import Any


MAX_FILE_BYTES = 100_000
MAX_TOTAL_BYTES = 500_000
MAVEN_TIMEOUT_SECONDS = 600
INTENT_ID = re.compile(r"^UT-[0-9]{3,}$")
JAVA_FQN = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)+$")
PACKAGE = re.compile(
    r"^\s*package\s+([A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)*)\s*;",
    re.MULTILINE,
)
TEST_ANNOTATION = re.compile(r"@(Test|ParameterizedTest|RepeatedTest)\b")
FORBIDDEN_TEST_PATTERNS = {
    "禁止停用測試": re.compile(r"@(Disabled|Ignore)\b"),
    "禁止用 assumption 跳過測試": re.compile(r"\b(Assumptions\.|assumeTrue\s*\(|assumeFalse\s*\()"),
    "禁止使用 Thread.sleep": re.compile(r"\bThread\.sleep\s*\("),
}
STRONG_ASSERTIONS = (
    re.compile(r"\bassertEquals\s*\("),
    re.compile(r"\bassertThrows\s*\("),
    re.compile(r"\bassertAll\s*\("),
    re.compile(r"\bassertArrayEquals\s*\("),
    re.compile(r"\bassertIterableEquals\s*\("),
    re.compile(r"\.isEqualTo\s*\("),
    re.compile(r"\.containsExactly(?:InAnyOrder)?\s*\("),
    re.compile(r"\.hasMessage\s*\("),
    re.compile(r"\bverify\s*\("),
)


class RequestError(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".")
    parser.add_argument("--session-id", default="manual")
    parser.add_argument(
        "--publish",
        action="store_true",
        help="通過後發布到原專案；省略時只驗證",
    )
    return parser.parse_args()


def safe_session_id(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_-]", "_", value)
    return normalized[:100] or "manual"


def next_report_directory(repo: Path, session_id: str) -> Path:
    session_root = repo / ".opencode/test-agent-runs" / safe_session_id(session_id)
    session_root.mkdir(parents=True, exist_ok=True)
    numbers = []
    for path in session_root.glob("attempt-*"):
        match = re.fullmatch(r"attempt-([0-9]+)", path.name)
        if match:
            numbers.append(int(match.group(1)))
    report = session_root / f"attempt-{max(numbers, default=0) + 1}"
    report.mkdir(parents=True)
    return report


def require_repo(value: str) -> Path:
    repo = Path(value).resolve()
    if repo != Path.cwd().resolve():
        raise RequestError("--repo 必須指向目前工作目錄")
    if not (repo / "pom.xml").is_file():
        raise RequestError("專案根目錄缺少 pom.xml")
    wrapper = repo / "mvnw"
    if not wrapper.is_file() and shutil.which("mvn") is None:
        raise RequestError("找不到 Maven Wrapper 或系統 mvn")
    if wrapper.is_file() and not os.access(wrapper, os.X_OK):
        raise RequestError("mvnw 沒有可執行權限")
    return repo


def read_request() -> dict[str, Any]:
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        raise RequestError(f"輸入不是有效 JSON：{exc}") from exc
    if not isinstance(data, dict):
        raise RequestError("輸入必須是 JSON 物件")
    return data


def require_string_list(
    data: dict[str, Any], key: str, *, minimum: int, maximum: int
) -> list[str]:
    value = data.get(key)
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise RequestError(f"{key} 數量必須介於 {minimum} 到 {maximum}")
    if not all(isinstance(item, str) and item.strip() for item in value):
        raise RequestError(f"{key} 只能包含非空字串")
    normalized = [item.strip() for item in value]
    if len(set(normalized)) != len(normalized):
        raise RequestError(f"{key} 不得重複")
    return normalized


def is_surefire_test_name(name: str) -> bool:
    stem = name.removesuffix(".java")
    return stem.startswith("Test") or stem.endswith(("Test", "Tests", "TestCase"))


def normalize_test_path(value: Any) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value:
        raise RequestError("測試路徑必須是使用 / 的非空相對路徑")
    path = PurePosixPath(value)
    if path.is_absolute() or path.as_posix() != value or any(part in {"", ".", ".."} for part in path.parts):
        raise RequestError(f"不安全的測試路徑：{value}")
    if path.suffix != ".java" or not is_surefire_test_name(path.name):
        raise RequestError(f"測試檔必須符合 Surefire 預設命名：{value}")
    parts = path.parts
    if not any(parts[index : index + 3] == ("src", "test", "java") for index in range(len(parts) - 2)):
        raise RequestError(f"測試路徑必須位於 src/test/java/**：{value}")
    return path


def class_name_for(path: PurePosixPath, content: str) -> str:
    parts = path.parts
    java_index = next(
        index + 3
        for index in range(len(parts) - 2)
        if parts[index : index + 3] == ("src", "test", "java")
    )
    relative_parts = parts[java_index:]
    expected_package = ".".join(relative_parts[:-1])
    match = PACKAGE.search(content)
    actual_package = match.group(1) if match else ""
    if actual_package != expected_package:
        raise RequestError(
            f"{path.as_posix()} 的 package 應為 {expected_package or '(default package)'}"
        )
    class_name = path.stem
    if not re.search(rf"\bclass\s+{re.escape(class_name)}\b", content):
        raise RequestError(f"{path.as_posix()} 缺少類別 {class_name}")
    return f"{actual_package}.{class_name}" if actual_package else class_name


def validate_content(path: PurePosixPath, content: Any, intent_ids: list[str]) -> str:
    if not isinstance(content, str) or not content.strip():
        raise RequestError(f"{path.as_posix()} 內容不得為空")
    size = len(content.encode("utf-8"))
    if size > MAX_FILE_BYTES:
        raise RequestError(f"{path.as_posix()} 超過 {MAX_FILE_BYTES} bytes")
    if not TEST_ANNOTATION.search(content):
        raise RequestError(f"{path.as_posix()} 至少需要一個 JUnit 測試註記")
    for message, pattern in FORBIDDEN_TEST_PATTERNS.items():
        if pattern.search(content):
            raise RequestError(f"{path.as_posix()}：{message}")
    if not any(pattern.search(content) for pattern in STRONG_ASSERTIONS):
        raise RequestError(f"{path.as_posix()} 缺少具體斷言")
    if not any(intent_id in content for intent_id in intent_ids):
        raise RequestError(f"{path.as_posix()} 必須註明至少一個已核准測試意圖編號")
    return content


def validate_request(repo: Path, data: dict[str, Any]) -> dict[str, Any]:
    intent_ids = require_string_list(data, "approved_intent_ids", minimum=1, maximum=50)
    invalid_ids = [item for item in intent_ids if not INTENT_ID.fullmatch(item)]
    if invalid_ids:
        raise RequestError(f"無效測試意圖編號：{', '.join(invalid_ids)}")

    target_classes = require_string_list(data, "target_classes", minimum=1, maximum=10)
    invalid_classes = [item for item in target_classes if not JAVA_FQN.fullmatch(item)]
    if invalid_classes:
        raise RequestError(
            "target_classes 必須是無萬用字元的完整類別名稱："
            + ", ".join(invalid_classes)
        )

    raw_files = data.get("files")
    if not isinstance(raw_files, list) or not 1 <= len(raw_files) <= 10:
        raise RequestError("files 數量必須介於 1 到 10")

    files: list[dict[str, str]] = []
    seen_paths: set[str] = set()
    total_bytes = 0
    candidate_classes: list[str] = []
    combined_content = ""
    for raw in raw_files:
        if not isinstance(raw, dict):
            raise RequestError("files 每一筆都必須是物件")
        path = normalize_test_path(raw.get("path"))
        relative = path.as_posix()
        if relative in seen_paths:
            raise RequestError(f"重複測試路徑：{relative}")
        destination = safe_destination(repo, path)
        if destination.exists():
            raise RequestError(f"第一版只允許新增測試檔，路徑已存在：{relative}")
        content = validate_content(path, raw.get("content"), intent_ids)
        class_name = class_name_for(path, content)
        total_bytes += len(content.encode("utf-8"))
        combined_content += content
        seen_paths.add(relative)
        candidate_classes.append(class_name)
        files.append({"path": relative, "content": content})

    if total_bytes > MAX_TOTAL_BYTES:
        raise RequestError(f"候選測試總量超過 {MAX_TOTAL_BYTES} bytes")
    missing_intents = [intent_id for intent_id in intent_ids if intent_id not in combined_content]
    if missing_intents:
        raise RequestError(
            "下列核准意圖未出現在候選測試中：" + ", ".join(missing_intents)
        )
    return {
        "approved_intent_ids": intent_ids,
        "target_classes": target_classes,
        "candidate_classes": candidate_classes,
        "files": files,
    }


def safe_destination(root: Path, relative: PurePosixPath) -> Path:
    root = root.resolve()
    destination = root.joinpath(*relative.parts)
    try:
        destination.resolve(strict=False).relative_to(root)
    except ValueError as exc:
        raise RequestError(f"路徑離開專案範圍：{relative.as_posix()}") from exc
    return destination


def copy_project(source: Path, destination: Path) -> None:
    def ignore(directory: str, names: list[str]) -> set[str]:
        ignored = {name for name in names if name in {".git", "target"}}
        if Path(directory).name == ".opencode" and "test-agent-runs" in names:
            ignored.add("test-agent-runs")
        return ignored

    shutil.copytree(source, destination, symlinks=True, ignore=ignore)


def write_candidates(root: Path, files: list[dict[str, str]]) -> None:
    for item in files:
        relative = PurePosixPath(item["path"])
        destination = safe_destination(root, relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f"{destination.name}.tmp-{os.getpid()}")
        temporary.write_text(item["content"], encoding="utf-8")
        os.replace(temporary, destination)


def publish_new_candidates(root: Path, files: list[dict[str, str]]) -> list[str]:
    published: list[Path] = []
    temporary_files: list[Path] = []
    try:
        for item in files:
            relative = PurePosixPath(item["path"])
            destination = safe_destination(root, relative)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                raise RequestError(f"發布前路徑已存在：{relative.as_posix()}")
            temporary = destination.with_name(f".{destination.name}.tmp-{os.getpid()}")
            with temporary.open("x", encoding="utf-8") as handle:
                handle.write(item["content"])
                handle.flush()
                os.fsync(handle.fileno())
            temporary_files.append(temporary)
            os.link(temporary, destination)
            temporary.unlink()
            temporary_files.remove(temporary)
            published.append(destination)
        return [path.relative_to(root).as_posix() for path in published]
    except Exception:
        for temporary in temporary_files:
            temporary.unlink(missing_ok=True)
        for destination in published:
            destination.unlink(missing_ok=True)
        raise


def relevant_workspace_files(repo: Path) -> list[Path]:
    files: list[Path] = []
    for path in repo.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        parts = path.relative_to(repo).parts
        if any(part in {".git", "target"} for part in parts):
            continue
        if len(parts) >= 2 and parts[0:2] == (".opencode", "test-agent-runs"):
            continue
        include = (
            path.name in {"pom.xml", "mvnw", "mvnw.cmd"}
            or ".mvn" in parts
            or any(
                parts[index : index + 2] in {("src", "main"), ("src", "test")}
                for index in range(len(parts) - 1)
            )
        )
        if include:
            files.append(path)
    return sorted(files)


def workspace_fingerprint(repo: Path) -> str:
    digest = hashlib.sha256()
    for path in relevant_workspace_files(repo):
        digest.update(path.relative_to(repo).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def maven_executable(repo: Path) -> str:
    wrapper = repo / "mvnw"
    return str(wrapper) if wrapper.is_file() else str(shutil.which("mvn"))


def command_environment() -> dict[str, str]:
    environment = {**os.environ, "CI": "true", "TERM": "dumb"}
    if environment.get("JAVA_HOME"):
        return environment
    candidates = (
        Path("/opt/homebrew/opt/openjdk/libexec/openjdk.jdk/Contents/Home"),
        Path("/usr/local/opt/openjdk/libexec/openjdk.jdk/Contents/Home"),
    )
    for java_home in candidates:
        java = java_home / "bin/java"
        if java.is_file() and os.access(java, os.X_OK):
            environment["JAVA_HOME"] = str(java_home)
            environment["PATH"] = f"{java.parent}{os.pathsep}{environment.get('PATH', '')}"
            break
    return environment


def run_command(
    *,
    repo: Path,
    arguments: list[str],
    stage: str,
    report_directory: Path,
) -> dict[str, Any]:
    command = [maven_executable(repo), *arguments]
    log_path = report_directory / f"{stage}.log"
    environment = command_environment()
    try:
        completed = subprocess.run(
            command,
            cwd=repo,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=environment,
            timeout=MAVEN_TIMEOUT_SECONDS,
        )
        output = completed.stdout + completed.stderr
        exit_code = completed.returncode
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else exc.stdout or ""
        stderr = exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else exc.stderr or ""
        output = stdout + stderr
        exit_code = 124
        timed_out = True
    if len(output) > 2_000_000:
        output = output[-2_000_000:]
    log_path.write_text(f"$ {shlex.join(command)}\n\n{output}", encoding="utf-8")
    return {
        "stage": stage,
        "command": shlex.join(command),
        "exit_code": exit_code,
        "timed_out": timed_out,
        "log": log_path.name,
        "tail": "\n".join(output.splitlines()[-80:]),
    }


def parse_surefire(repo: Path) -> dict[str, int]:
    result = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    for report in repo.rglob("TEST-*.xml"):
        if report.parent.name != "surefire-reports" or report.parent.parent.name != "target":
            continue
        try:
            suite = ET.parse(report).getroot()
        except (ET.ParseError, OSError):
            continue
        for key in result:
            try:
                result[key] += int(float(suite.attrib.get(key, "0")))
            except ValueError:
                pass
    return result


def declared_capabilities(repo: Path) -> dict[str, bool]:
    found = {"jacoco": False, "pit": False}
    known = {
        "jacoco-maven-plugin": "jacoco",
        "pitest-maven": "pit",
    }
    for pom in repo.rglob("pom.xml"):
        parts = pom.relative_to(repo).parts
        if any(part in {".git", ".opencode", "node_modules", "target"} for part in parts):
            continue
        try:
            root = ET.parse(pom).getroot()
        except (ET.ParseError, OSError):
            continue
        namespace = root.tag.partition("}")[0].removeprefix("{")

        def qname(name: str) -> str:
            return f"{{{namespace}}}{name}" if namespace else name

        build = root.find(qname("build"))
        plugins = build.find(qname("plugins")) if build is not None else None
        if plugins is None:
            continue
        for plugin in plugins.findall(qname("plugin")):
            artifact = plugin.find(qname("artifactId"))
            if artifact is None or not artifact.text:
                continue
            capability = known.get(artifact.text.strip())
            if capability:
                found[capability] = True
    return found


def parse_coverage(repo: Path, target_classes: list[str]) -> dict[str, Any] | None:
    totals = {
        "line_missed": 0,
        "line_covered": 0,
        "branch_missed": 0,
        "branch_covered": 0,
    }
    matched: set[str] = set()
    for report in repo.rglob("jacoco.csv"):
        if report.parent.name != "jacoco" or report.parent.parent.name != "site":
            continue
        with report.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                name = f"{row['PACKAGE']}.{row['CLASS']}"
                if name not in target_classes:
                    continue
                matched.add(name)
                totals["line_missed"] += int(row["LINE_MISSED"])
                totals["line_covered"] += int(row["LINE_COVERED"])
                totals["branch_missed"] += int(row["BRANCH_MISSED"])
                totals["branch_covered"] += int(row["BRANCH_COVERED"])
    if not matched:
        return None
    line_total = totals["line_missed"] + totals["line_covered"]
    branch_total = totals["branch_missed"] + totals["branch_covered"]
    return {
        "target_classes": sorted(matched),
        **totals,
        "line_ratio": totals["line_covered"] / line_total if line_total else None,
        "branch_ratio": totals["branch_covered"] / branch_total if branch_total else None,
    }


def parse_mutations(repo: Path) -> dict[str, Any] | None:
    total = 0
    killed = 0
    reports = []
    pattern = re.compile(r'coverage_legend">([0-9]+)/([0-9]+)</div>')
    for report in repo.rglob("pit-reports/index.html"):
        text = report.read_text(encoding="utf-8", errors="replace")
        matches = pattern.findall(text)
        if len(matches) < 2:
            continue
        report_killed, report_total = (int(value) for value in matches[1])
        killed += report_killed
        total += report_total
        reports.append(report.relative_to(repo).as_posix())
    if not reports or total == 0:
        return None
    return {
        "total": total,
        "killed": killed,
        "survived": total - killed,
        "score": killed / total,
        "reports": reports,
    }


def copy_artifacts(repo: Path, report_directory: Path) -> None:
    artifact_root = report_directory / "artifacts"
    for target in repo.rglob("target"):
        if not target.is_dir():
            continue
        module = target.parent.relative_to(repo)
        for relative in (
            Path("surefire-reports"),
            Path("site/jacoco"),
            Path("pit-reports"),
        ):
            source = target / relative
            if not source.exists():
                continue
            destination = artifact_root / module / "target" / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            if source.is_dir():
                shutil.copytree(source, destination, dirs_exist_ok=True)
            else:
                shutil.copy2(source, destination)


def persist_result(
    *,
    repo: Path,
    report_directory: Path,
    result: dict[str, Any],
    isolated_repo: Path | None = None,
) -> None:
    if isolated_repo is not None and isolated_repo.exists():
        copy_artifacts(isolated_repo, report_directory)
    result["report_directory"] = report_directory.relative_to(repo).as_posix()
    result_path = report_directory / "result.json"
    result_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


def failure_result(
    status: str,
    message: str,
    *,
    commands: list[dict[str, Any]],
    **extra: Any,
) -> dict[str, Any]:
    result = {"status": status, "message": message, "commands": commands, **extra}
    failed = next((command for command in reversed(commands) if command["exit_code"] != 0), None)
    if failed:
        result["failure_tail"] = failed["tail"]
    for command in result["commands"]:
        command.pop("tail", None)
    return result


def main() -> int:
    args = parse_args()
    repo = require_repo(args.repo)
    report_directory = next_report_directory(repo, args.session_id)
    commands: list[dict[str, Any]] = []
    isolated_repo: Path | None = None
    try:
        request = validate_request(repo, read_request())
        initial_fingerprint = workspace_fingerprint(repo)
        capabilities = declared_capabilities(repo)

        with tempfile.TemporaryDirectory(prefix="unit-test-submit-") as temporary:
            isolated_repo = Path(temporary) / "project"
            copy_project(repo, isolated_repo)

            baseline = run_command(
                repo=isolated_repo,
                arguments=["-B", "-ntp", "test"],
                stage="01-baseline-test",
                report_directory=report_directory,
            )
            commands.append(baseline)
            baseline_tests = parse_surefire(isolated_repo)
            if baseline["exit_code"] != 0 or baseline_tests["failures"] or baseline_tests["errors"]:
                result = failure_result(
                    "baseline-failed",
                    "既有測試基準失敗，沒有寫入候選測試。",
                    commands=commands,
                    baseline=baseline_tests,
                )
                persist_result(
                    repo=repo,
                    report_directory=report_directory,
                    result=result,
                    isolated_repo=isolated_repo,
                )
                return 3

            write_candidates(isolated_repo, request["files"])
            compile_result = run_command(
                repo=isolated_repo,
                arguments=["-B", "-ntp", "-DskipTests", "test-compile"],
                stage="02-candidate-compile",
                report_directory=report_directory,
            )
            commands.append(compile_result)
            if compile_result["exit_code"] != 0:
                result = failure_result(
                    "candidate-compile-failed",
                    "候選測試編譯失敗，沒有發布測試檔。",
                    commands=commands,
                    baseline=baseline_tests,
                )
                persist_result(
                    repo=repo,
                    report_directory=report_directory,
                    result=result,
                    isolated_repo=isolated_repo,
                )
                return 4

            test_result = run_command(
                repo=isolated_repo,
                arguments=["-B", "-ntp", "test"],
                stage="03-candidate-test",
                report_directory=report_directory,
            )
            commands.append(test_result)
            candidate_tests = parse_surefire(isolated_repo)
            if (
                test_result["exit_code"] != 0
                or candidate_tests["failures"]
                or candidate_tests["errors"]
            ):
                result = failure_result(
                    "candidate-tests-failed",
                    "候選測試失敗；可能是測試錯誤或正式程式與核准規格衝突。工具沒有發布或調整預期值。",
                    commands=commands,
                    baseline=baseline_tests,
                    tests=candidate_tests,
                )
                persist_result(
                    repo=repo,
                    report_directory=report_directory,
                    result=result,
                    isolated_repo=isolated_repo,
                )
                return 5
            if candidate_tests["tests"] <= baseline_tests["tests"]:
                result = failure_result(
                    "candidate-tests-not-executed",
                    "候選測試沒有增加實際執行的測試數量，因此不發布。",
                    commands=commands,
                    baseline=baseline_tests,
                    tests=candidate_tests,
                )
                persist_result(
                    repo=repo,
                    report_directory=report_directory,
                    result=result,
                    isolated_repo=isolated_repo,
                )
                return 6

            verify_result = run_command(
                repo=isolated_repo,
                arguments=["-B", "-ntp", "verify"],
                stage="04-verify-and-jacoco",
                report_directory=report_directory,
            )
            commands.append(verify_result)
            verified_tests = parse_surefire(isolated_repo)
            if verify_result["exit_code"] != 0:
                result = failure_result(
                    "verify-failed",
                    "Maven verify 失敗，沒有發布測試檔。",
                    commands=commands,
                    baseline=baseline_tests,
                    tests=verified_tests,
                )
                persist_result(
                    repo=repo,
                    report_directory=report_directory,
                    result=result,
                    isolated_repo=isolated_repo,
                )
                return 7

            coverage = None
            if capabilities["jacoco"]:
                coverage = parse_coverage(isolated_repo, request["target_classes"])
                if coverage is None:
                    result = failure_result(
                        "coverage-report-missing",
                        "pom.xml 已宣告 JaCoCo，但找不到目標類別報告，因此不發布。",
                        commands=commands,
                        baseline=baseline_tests,
                        tests=verified_tests,
                    )
                    persist_result(
                        repo=repo,
                        report_directory=report_directory,
                        result=result,
                        isolated_repo=isolated_repo,
                    )
                    return 8

            mutation = None
            if capabilities["pit"]:
                pit_result = run_command(
                    repo=isolated_repo,
                    arguments=[
                        "-B",
                        "-ntp",
                        f"-DtargetClasses={','.join(request['target_classes'])}",
                        f"-DtargetTests={','.join(request['candidate_classes'])}",
                        "org.pitest:pitest-maven:mutationCoverage",
                    ],
                    stage="05-limited-pit",
                    report_directory=report_directory,
                )
                commands.append(pit_result)
                if pit_result["exit_code"] != 0:
                    result = failure_result(
                        "mutation-run-failed",
                        "限定範圍 PIT 執行失敗，沒有發布測試檔。",
                        commands=commands,
                        baseline=baseline_tests,
                        tests=verified_tests,
                        coverage=coverage,
                    )
                    persist_result(
                        repo=repo,
                        report_directory=report_directory,
                        result=result,
                        isolated_repo=isolated_repo,
                    )
                    return 9
                mutation = parse_mutations(isolated_repo)
                if mutation is None:
                    result = failure_result(
                        "mutation-report-missing",
                        "PIT 沒有產生可解析的突變結果，因此不發布。",
                        commands=commands,
                        baseline=baseline_tests,
                        tests=verified_tests,
                        coverage=coverage,
                    )
                    persist_result(
                        repo=repo,
                        report_directory=report_directory,
                        result=result,
                        isolated_repo=isolated_repo,
                    )
                    return 10

            if workspace_fingerprint(repo) != initial_fingerprint:
                result = failure_result(
                    "workspace-changed",
                    "驗證期間原專案的正式程式、建置設定或既有測試發生變更，因此不發布。",
                    commands=commands,
                    baseline=baseline_tests,
                    tests=verified_tests,
                    coverage=coverage,
                    mutation=mutation,
                )
                persist_result(
                    repo=repo,
                    report_directory=report_directory,
                    result=result,
                    isolated_repo=isolated_repo,
                )
                return 11

            published_files: list[str] = []
            if args.publish:
                published_files = publish_new_candidates(repo, request["files"])

            result = {
                "status": "published" if args.publish else "validated",
                "message": (
                    "候選測試已通過隔離基準、編譯、測試與已設定的品質工具，並發布新測試檔。"
                    if args.publish
                    else "候選測試已通過隔離驗證；本次未發布。"
                ),
                "approved_intent_ids": request["approved_intent_ids"],
                "target_classes": request["target_classes"],
                "published_files": published_files,
                "baseline": baseline_tests,
                "tests": verified_tests,
                "coverage": coverage
                if capabilities["jacoco"]
                else {"status": "skipped", "reason": "pom.xml 未直接宣告 JaCoCo"},
                "mutation": mutation
                if capabilities["pit"]
                else {"status": "skipped", "reason": "pom.xml 未直接宣告 PIT"},
                "commands": commands,
            }
            for command in result["commands"]:
                command.pop("tail", None)
            persist_result(
                repo=repo,
                report_directory=report_directory,
                result=result,
                isolated_repo=isolated_repo,
            )
            return 0
    except RequestError as exc:
        result = failure_result(
            "invalid-request",
            str(exc),
            commands=commands,
        )
        persist_result(
            repo=repo,
            report_directory=report_directory,
            result=result,
            isolated_repo=isolated_repo,
        )
        return 2
    except Exception as exc:
        trace = traceback.format_exc()
        (report_directory / "internal-error.log").write_text(trace, encoding="utf-8")
        result = failure_result(
            "internal-error",
            f"驗證工具發生未預期錯誤：{exc}",
            commands=commands,
        )
        persist_result(
            repo=repo,
            report_directory=report_directory,
            result=result,
            isolated_repo=isolated_repo,
        )
        return 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
