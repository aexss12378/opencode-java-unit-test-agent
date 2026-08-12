"""在指定 worktree 中驗證單一 Java 型別的候選測試。"""

from __future__ import annotations

import argparse
import json
import os
import signal
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

MAVEN_TIMEOUT_SECONDS = 600
MINIMUM_LINE_COVERAGE_PERCENT = 80

_ACTIVE_PROCESS: subprocess.Popen[str] | None = None
_CANCEL_REQUESTED = False


class RequestError(RuntimeError):
    """可安全回傳給代理的輸入或環境錯誤。"""


@dataclass(frozen=True)
class Target:
    worktree: Path
    relative_worktree: str
    target_class: str
    candidate_class: str
    test_file: str


def cancelled() -> bool:
    return _CANCEL_REQUESTED


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
    if _ACTIVE_PROCESS is not None:
        terminate_process(_ACTIVE_PROCESS)


def install_signal_handlers() -> None:
    signal.signal(signal.SIGTERM, request_cancellation)
    signal.signal(signal.SIGINT, request_cancellation)


def run_command(
    command: list[str],
    *,
    cwd: Path,
    timeout: int,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    global _ACTIVE_PROCESS
    if cancelled():
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
    _ACTIVE_PROCESS = process
    try:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
            return_code = process.returncode
        except subprocess.TimeoutExpired:
            terminate_process(process)
            stdout, stderr = process.communicate()
            return_code = 124
        if cancelled():
            return_code = 130
            stderr = (stderr + "\n工作已取消").lstrip("\n")
        return subprocess.CompletedProcess(command, return_code, stdout, stderr)
    finally:
        _ACTIVE_PROCESS = None


def candidate_path(target_class: str) -> PurePosixPath:
    package, _, simple_name = target_class.rpartition(".")
    return PurePosixPath(
        "src", "test", "java", *package.split("."), f"{simple_name}Test.java"
    )


def load_target(repo: Path, data: dict[str, Any]) -> Target:
    target_class = data["target_class"]
    relative_worktree = data["worktree"]
    test_file = candidate_path(target_class).as_posix()
    return Target(
        worktree=(repo / relative_worktree).resolve(),
        relative_worktree=relative_worktree,
        target_class=target_class,
        candidate_class=f"{target_class}Test",
        test_file=test_file,
    )


def validate_test_cases(data: dict[str, Any]) -> list[dict[str, str]]:
    cases = data["test_cases"]
    ids = [case["id"] for case in cases]
    if len(ids) != len(set(ids)):
        raise RequestError("測試案例編號不得重複")
    return cases


def validate_candidate(target: Target, data: dict[str, Any]) -> None:
    cases = validate_test_cases(data)
    path = target.worktree.joinpath(*PurePosixPath(target.test_file).parts)
    content = path.read_text(encoding="utf-8")
    missing_ids = [case["id"] for case in cases if case["id"] not in content]
    if missing_ids:
        raise RequestError("候選測試缺少案例編號：" + ", ".join(missing_ids))


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
    if target.exists():
        shutil.rmtree(target)


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
            env={**os.environ, "CI": "true", "TERM": "dumb"},
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
    target: Target,
    validation: dict[str, Any] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": status,
        "message": message,
        "target_class": target.target_class,
        "test_file": target.test_file,
        "worktree": target.relative_worktree,
        **extra,
    }
    if validation is not None:
        result["validation"] = validation
    return result


def validate(target: Target, data: dict[str, Any]) -> dict[str, Any]:
    validate_candidate(target, data)
    clear_maven_outputs(target.worktree)
    maven = run_maven(target.worktree, target.candidate_class)

    validation: dict[str, Any] = {
        key: maven[key] for key in ("command", "exit_code", "timed_out")
    }
    if maven["exit_code"] == 130 and cancelled():
        return failure("cancelled", "單元測試工作已取消。", target, validation)
    if maven["exit_code"] != 0:
        return failure(
            "candidate-check-failed",
            "候選測試未通過 Maven test；請修正測試編譯或設定錯誤。可信規格與實作衝突時，不得修改預期結果迎合實作。",
            target,
            validation,
            diagnostic_field="maven_errors",
            maven_errors=maven["maven_errors"],
        )

    summary = test_summary(target.worktree, target.candidate_class)
    validation["candidate_tests"] = summary
    if summary["tests"] == 0 or summary["skipped"]:
        return failure(
            "candidate-not-executed",
            "Maven 成功，但候選測試沒有全部實際執行。",
            target,
            validation,
        )
    try:
        coverage = coverage_summary(target.worktree, target.target_class)
    except RequestError as exc:
        return failure("coverage-report-invalid", str(exc), target, validation)
    validation["coverage"] = coverage
    if not coverage["passed"]:
        return failure(
            "coverage-below-threshold",
            f"目標類別行覆蓋率為 {coverage['percent']:.2f}%，低於 {coverage['minimum_percent']}% 門檻。",
            target,
            validation,
        )

    return {
        "status": "validation-passed",
        "message": "候選測試已通過 Maven、Surefire 與 JaCoCo 驗證。",
        "target_class": target.target_class,
        "test_file": target.test_file,
        "worktree": target.relative_worktree,
        "validation": validation,
    }


def main() -> int:
    install_signal_handlers()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".")
    args = parser.parse_args()
    target: Target | None = None
    try:
        repo = Path(args.repo).resolve()
        data = json.load(sys.stdin)
        target = load_target(repo, data)
        result = validate(target, data)
        successful = result["status"] == "validation-passed"
    except (RequestError, OSError, UnicodeError) as exc:
        result: dict[str, Any] = {
            "status": "cancelled" if cancelled() else "validation-failed",
            "message": str(exc),
        }
        if target is not None:
            result.update(
                {
                    "target_class": target.target_class,
                    "test_file": target.test_file,
                    "worktree": target.relative_worktree,
                }
            )
        successful = False
    except Exception as exc:  # noqa: BLE001 - CLI 邊界需回傳結構化錯誤
        result = {"status": "internal-error", "message": str(exc)}
        successful = False
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if successful else 3


if __name__ == "__main__":
    raise SystemExit(main())
