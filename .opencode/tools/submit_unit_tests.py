#!/usr/bin/env python3
"""在隔離副本驗證一個 Java 候選測試，核准後才發布。"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path, PurePosixPath
from typing import Any


MAX_FILE_BYTES = 100_000
MAVEN_TIMEOUT_SECONDS = 600
MINIMUM_LINE_COVERAGE_PERCENT = 80
CASE_ID = re.compile(r"^UT-[0-9]{3,}$")
PACKAGE = re.compile(
    r"^\s*package\s+([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)\s*;",
    re.MULTILINE,
)


class RequestError(RuntimeError):
    pass


def repo_root(value: str) -> Path:
    repo = Path(value).resolve()
    if repo != Path.cwd().resolve():
        raise RequestError("--repo 必須指向目前工作目錄")
    if not (repo / "pom.xml").is_file():
        raise RequestError("專案根目錄缺少 pom.xml")
    wrapper = repo / "mvnw"
    if not wrapper.is_file() or not os.access(wrapper, os.X_OK):
        raise RequestError("專案根目錄需要可執行的 mvnw")
    return repo


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


def managed(repo: Path, name: str) -> Path:
    if (repo / ".opencode").is_symlink():
        raise RequestError(".opencode 不得為符號連結")
    return repo / ".opencode" / name


def reset(path: Path) -> None:
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        raise RequestError(f"工具路徑不是一般目錄：{path}")
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(mode=0o700, parents=True)


def clean(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, ignore_errors=True)


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


def copy_project(repo: Path, project: Path) -> None:
    shutil.copytree(
        repo,
        project,
        symlinks=True,
        ignore=shutil.ignore_patterns(".git", ".opencode", "target"),
    )


def run_maven(project: Path, candidate_class: str) -> dict[str, Any]:
    environment = {**os.environ, "CI": "true", "TERM": "dumb", "PWD": str(project)}
    environment.pop("OLDPWD", None)
    environment.pop("INIT_CWD", None)
    try:
        result = subprocess.run(
            [str(project / "mvnw"), "-B", "-ntp", f"-Dtest={candidate_class}", "test"],
            cwd=project,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=environment,
            timeout=MAVEN_TIMEOUT_SECONDS,
        )
        output = result.stdout + result.stderr
        exit_code, timed_out = result.returncode, False
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else exc.stdout or ""
        stderr = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else exc.stderr or ""
        output, exit_code, timed_out = stdout + stderr, 124, True
    return {
        "command": f"./mvnw -B -ntp -Dtest={candidate_class} test",
        "exit_code": exit_code,
        "timed_out": timed_out,
        "failure_tail": "\n".join(output[-2_000_000:].splitlines()[-80:]),
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


def review_files(repo: Path, request: dict[str, Any], validation: dict[str, Any]) -> Path:
    review = managed(repo, "unit-test-review")
    reset(review)
    sections = []
    for case in request["test_cases"]:
        sections.append(
            f"## {case['id']}\n\n"
            f"- 情境：{case['scenario']}\n"
            f"- 預期：{case['expected']}\n"
            f"- 依據：{case['evidence']}"
        )
    cases = (
        "# 候選單元測試\n\n"
        f"受測類別：`{request['target_class']}`\n\n"
        f"測試檔：`{request['file']['path']}`\n\n"
        f"驗證指令：`{validation['command']}`\n\n"
        f"實際執行：{validation['candidate_tests']['executed']} 個測試\n\n"
        f"行覆蓋率：{validation['coverage']['percent']:.2f}%"
        f"（門檻：{validation['coverage']['minimum_percent']}%）\n\n"
        + "\n\n".join(sections)
        + "\n\n審查資料只能用來查看；需要修改時請拒絕並告訴 Agent 原因。\n"
    )
    atomic_write(review / "cases.md", cases)

    relative = PurePosixPath(request["file"]["path"])
    current = destination(repo, relative)
    old = current.read_text(encoding="utf-8") if current.is_file() else ""
    diff = "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            request["file"]["content"].splitlines(keepends=True),
            fromfile=f"a/{relative}" if current.is_file() else "/dev/null",
            tofile=f"b/{relative}",
        )
    )
    atomic_write(review / "changes.diff", diff)
    write_candidate(review, request["file"])
    return review


def review(repo: Path, request: dict[str, Any]) -> dict[str, Any]:
    review_dir = managed(repo, "unit-test-review")
    clean(review_dir)
    with tempfile.TemporaryDirectory(prefix="unit-test-work-") as temporary:
        project = Path(temporary) / "project"
        copy_project(repo, project)
        write_candidate(project, request["file"])
        maven = run_maven(project, request["candidate_class"])
        validation = {key: maven[key] for key in ("command", "exit_code", "timed_out")}
        if maven["exit_code"] != 0:
            return {
                "status": "candidate-check-failed",
                "message": "候選測試未通過 Maven test；請 Agent 修正後重新提交。",
                "validation": validation,
                "failure_tail": maven["failure_tail"],
                "published": False,
            }

        summary = test_summary(project, request["candidate_class"])
        validation["candidate_tests"] = summary
        if summary["tests"] == 0 or summary["skipped"]:
            return {
                "status": "candidate-not-executed",
                "message": "Maven 成功，但候選測試沒有全部實際執行。",
                "validation": validation,
                "published": False,
            }
        if summary["unexpected_classes"]:
            return {
                "status": "candidate-not-isolated",
                "message": "Maven 執行了候選類別以外的測試，無法單獨計算候選測試覆蓋率。",
                "validation": validation,
                "published": False,
            }

        try:
            coverage = coverage_summary(project, request["target_class"])
        except RequestError as exc:
            return {
                "status": "coverage-report-invalid",
                "message": str(exc),
                "validation": validation,
                "published": False,
            }
        validation["coverage"] = coverage
        if not coverage["passed"]:
            return {
                "status": "coverage-below-threshold",
                "message": (
                    f"候選測試對 {request['target_class']} 的行覆蓋率為 "
                    f"{coverage['percent']:.2f}%，低於 {coverage['minimum_percent']}% 門檻。"
                ),
                "validation": validation,
                "published": False,
            }

        review_dir = review_files(repo, request, validation)
        candidate = destination(review_dir, PurePosixPath(request["file"]["path"]))
        return {
            "status": "awaiting-approval",
            "message": "候選測試已通過 Maven test，請在 IDE 審查後核准或拒絕。",
            "review_directory": str(review_dir),
            "review_files": [
                str(review_dir / "cases.md"),
                str(review_dir / "changes.diff"),
                str(candidate),
            ],
            "validation": validation,
            "published": False,
        }


def publish(repo: Path, request: dict[str, Any]) -> dict[str, Any]:
    relative = PurePosixPath(request["file"]["path"])
    target = destination(repo, relative)
    change = "updated" if target.is_file() else "created"
    atomic_write(target, request["file"]["content"])
    clean(managed(repo, "unit-test-review"))
    return {
        "status": "published",
        "message": "已發布工程師核准且通過 Maven test 的候選單元測試。",
        "published": True,
        "published_file": relative.as_posix(),
        "change": change,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("review", "publish"))
    parser.add_argument("--repo", default=".")
    args = parser.parse_args()
    try:
        repo = repo_root(args.repo)
        request = validate_request(repo)
        result = review(repo, request) if args.action == "review" else publish(repo, request)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["status"] in {"awaiting-approval", "published"} else 3
    except (RequestError, OSError, UnicodeError) as exc:
        print(json.dumps({"status": "invalid-request", "message": str(exc), "published": False}, ensure_ascii=False, indent=2))
        return 2
    except Exception as exc:
        print(json.dumps({"status": "internal-error", "message": str(exc), "published": False}, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
