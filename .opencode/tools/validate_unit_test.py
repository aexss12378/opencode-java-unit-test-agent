"""在指定 Service 的獨立工作樹中驗證唯一候選測試。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path, PurePosixPath
from typing import Any

from _unit_test_common import (
    Assignment,
    RequestError,
    cancelled,
    candidate_snapshot,
    changed_paths,
    destination,
    install_signal_handlers,
    load_assignment,
    parse_required_string,
    read_input,
    repo_root,
    require_only_path,
    run_command,
    save_assignment_state,
    validate_session_id,
    verify_assignment_state,
)

MAVEN_TIMEOUT_SECONDS = 600
MINIMUM_LINE_COVERAGE_PERCENT = 80


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
    result = run_command(
        [str(project / "mvnw"), "-B", "-ntp", f"-Dtest={candidate_class}", "test"],
        cwd=project,
        timeout=MAVEN_TIMEOUT_SECONDS,
        env=maven_environment(project),
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
    verify_assignment_state(assignment, require_base_head=True)
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
