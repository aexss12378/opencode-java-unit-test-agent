#!/usr/bin/env python3
"""唯讀檢查 Maven 專案可用的測試框架與品質工具。"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Iterable


EXCLUDED_DIRS = {".git", ".idea", ".opencode", "node_modules", "target"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".", help="專案根目錄；必須是目前工作目錄")
    parser.add_argument("--pretty", action="store_true", help="縮排輸出 JSON")
    return parser.parse_args()


def run(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def qname(namespace: str, name: str) -> str:
    return f"{{{namespace}}}{name}" if namespace else name


def element_text(parent: ET.Element | None, namespace: str, name: str) -> str | None:
    if parent is None:
        return None
    child = parent.find(qname(namespace, name))
    if child is None or child.text is None:
        return None
    value = child.text.strip()
    return value or None


def find_nodes(root: ET.Element, namespace: str, segments: Iterable[str]) -> list[ET.Element]:
    path = "/".join(qname(namespace, segment) for segment in segments)
    return list(root.findall(path))


def collect_dependencies(root: ET.Element, namespace: str) -> list[dict[str, Any]]:
    locations = [
        ("declared", ("dependencies", "dependency")),
        ("managed", ("dependencyManagement", "dependencies", "dependency")),
        ("profile-declared", ("profiles", "profile", "dependencies", "dependency")),
        (
            "profile-managed",
            ("profiles", "profile", "dependencyManagement", "dependencies", "dependency"),
        ),
    ]
    dependencies: list[dict[str, Any]] = []
    for source, segments in locations:
        for node in find_nodes(root, namespace, segments):
            dependencies.append(
                {
                    "source": source,
                    "group_id": element_text(node, namespace, "groupId"),
                    "artifact_id": element_text(node, namespace, "artifactId"),
                    "version": element_text(node, namespace, "version"),
                    "scope": element_text(node, namespace, "scope"),
                }
            )
    return dependencies


def collect_plugins(root: ET.Element, namespace: str) -> list[dict[str, Any]]:
    locations = [
        ("declared", ("build", "plugins", "plugin")),
        ("managed", ("build", "pluginManagement", "plugins", "plugin")),
        ("profile-declared", ("profiles", "profile", "build", "plugins", "plugin")),
        (
            "profile-managed",
            ("profiles", "profile", "build", "pluginManagement", "plugins", "plugin"),
        ),
    ]
    plugins: list[dict[str, Any]] = []
    for source, segments in locations:
        for node in find_nodes(root, namespace, segments):
            plugins.append(
                {
                    "source": source,
                    "group_id": element_text(node, namespace, "groupId"),
                    "artifact_id": element_text(node, namespace, "artifactId"),
                    "version": element_text(node, namespace, "version"),
                }
            )
    return plugins


def classify_libraries(dependencies: list[dict[str, Any]]) -> dict[str, list[str]]:
    found: dict[str, set[str]] = {
        "test_frameworks": set(),
        "mocking_libraries": set(),
        "assertion_libraries": set(),
    }
    for dependency in dependencies:
        # 未解析實際啟用的 Maven profile 前，不能把 profile 內的相依套件
        # 當成目前可用能力。
        if dependency["source"] != "declared":
            continue
        group_id = dependency.get("group_id") or ""
        artifact_id = dependency.get("artifact_id") or ""
        coordinate = f"{group_id}:{artifact_id}"

        if group_id == "org.junit.jupiter" or artifact_id.startswith("junit-jupiter"):
            found["test_frameworks"].add(f"JUnit 5 ({coordinate})")
        elif group_id == "junit" and artifact_id == "junit":
            found["test_frameworks"].add(f"JUnit 3/4 ({coordinate})")
        elif group_id == "org.testng" or artifact_id == "testng":
            found["test_frameworks"].add(f"TestNG ({coordinate})")

        if group_id == "org.mockito" or artifact_id.startswith("mockito-"):
            found["mocking_libraries"].add(f"Mockito ({coordinate})")
        elif group_id == "org.powermock" or artifact_id.startswith("powermock-"):
            found["mocking_libraries"].add(f"PowerMock ({coordinate})")

        if group_id == "org.assertj" or artifact_id == "assertj-core":
            found["assertion_libraries"].add(f"AssertJ ({coordinate})")
        elif group_id == "org.hamcrest" or artifact_id.startswith("hamcrest"):
            found["assertion_libraries"].add(f"Hamcrest ({coordinate})")

    return {key: sorted(values) for key, values in found.items()}


def classify_plugins(plugins: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    known = {
        "maven-surefire-plugin": "surefire",
        "maven-failsafe-plugin": "failsafe",
        "jacoco-maven-plugin": "jacoco",
        "pitest-maven": "pit",
    }
    result: dict[str, list[dict[str, Any]]] = {value: [] for value in known.values()}
    for plugin in plugins:
        category = known.get(plugin.get("artifact_id") or "")
        if category:
            result[category].append(plugin)
    return result


def collect_properties(root: ET.Element, namespace: str) -> dict[str, str]:
    properties = root.find(qname(namespace, "properties"))
    if properties is None:
        return {}
    result: dict[str, str] = {}
    for child in properties:
        if child.text and child.text.strip():
            key = child.tag.split("}", 1)[-1]
            result[key] = child.text.strip()
    interesting = {
        "java.version",
        "maven.compiler.release",
        "maven.compiler.source",
        "maven.compiler.target",
    }
    return {key: value for key, value in result.items() if key in interesting}


def parse_pom(repo: Path, pom: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        root = ET.parse(pom).getroot()
    except (ET.ParseError, OSError) as exc:
        return None, f"{pom.relative_to(repo).as_posix()}: {exc}"

    namespace = root.tag[1:].split("}", 1)[0] if root.tag.startswith("{") else ""
    dependencies = collect_dependencies(root, namespace)
    plugins = collect_plugins(root, namespace)
    build = root.find(qname(namespace, "build"))
    custom_test_source = element_text(build, namespace, "testSourceDirectory")
    relative_pom = pom.relative_to(repo).as_posix()
    module_root = pom.parent.relative_to(repo)
    conventional_test_root = (module_root / "src/test").as_posix()
    if conventional_test_root == ".":
        conventional_test_root = "src/test"

    modules = [
        node.text.strip()
        for node in find_nodes(root, namespace, ("modules", "module"))
        if node.text and node.text.strip()
    ]
    libraries = classify_libraries(dependencies)

    return (
        {
            "pom": relative_pom,
            "coordinates": {
                "group_id": element_text(root, namespace, "groupId"),
                "artifact_id": element_text(root, namespace, "artifactId"),
                "version": element_text(root, namespace, "version"),
                "packaging": element_text(root, namespace, "packaging") or "jar",
            },
            "modules": modules,
            "java_properties": collect_properties(root, namespace),
            "test_roots": {
                "conventional": conventional_test_root,
                "custom_declared": custom_test_source,
                "supported_by_default_permissions": custom_test_source in {None, "src/test/java"},
            },
            "libraries": libraries,
            "plugins": classify_plugins(plugins),
            "declared_dependencies": dependencies,
        },
        None,
    )


def find_poms(repo: Path) -> list[Path]:
    poms: list[Path] = []
    for path in repo.rglob("pom.xml"):
        relative_parts = path.relative_to(repo).parts
        if any(part in EXCLUDED_DIRS for part in relative_parts[:-1]):
            continue
        poms.append(path)
    return sorted(poms)


def inspect_git(repo: Path) -> dict[str, Any]:
    root_result = run(["git", "rev-parse", "--show-toplevel"], repo)
    if root_result.returncode != 0:
        return {
            "available": False,
            "root": None,
            "clean": None,
            "status_entries": None,
        }
    status_result = run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        repo,
    )
    entries = [line for line in status_result.stdout.splitlines() if line]
    return {
        "available": True,
        "root": root_result.stdout.strip(),
        "clean": status_result.returncode == 0 and not entries,
        "status_entries": entries,
    }


def main() -> int:
    args = parse_args()
    repo = Path(args.repo).resolve()
    cwd = Path.cwd().resolve()
    if repo != cwd:
        print(json.dumps({"status": "blocked", "blockers": ["--repo 必須指向目前工作目錄"]}))
        return 2
    if not repo.is_dir():
        print(json.dumps({"status": "blocked", "blockers": ["專案根目錄不存在"]}))
        return 2

    pom_paths = find_poms(repo)
    modules: list[dict[str, Any]] = []
    parse_errors: list[str] = []
    for pom in pom_paths:
        module, error = parse_pom(repo, pom)
        if module:
            modules.append(module)
        if error:
            parse_errors.append(error)

    git = inspect_git(repo)
    wrapper = repo / "mvnw"
    system_maven = shutil.which("mvn")
    all_frameworks = sorted(
        {
            framework
            for module in modules
            for framework in module["libraries"]["test_frameworks"]
        }
    )
    jacoco_active = any(
        plugin["source"] == "declared"
        for module in modules
        for plugin in module["plugins"]["jacoco"]
    )
    pit_active = any(
        plugin["source"] == "declared"
        for module in modules
        for plugin in module["plugins"]["pit"]
    )
    custom_test_roots = [
        module["pom"]
        for module in modules
        if not module["test_roots"]["supported_by_default_permissions"]
    ]

    blockers: list[str] = []
    warnings: list[str] = []
    if not pom_paths:
        blockers.append("目前目錄下沒有 pom.xml；此技能第一版只支援 Maven 專案")
    if parse_errors:
        blockers.append("有 pom.xml 無法解析，不能可靠判定測試能力")
    if not git["available"]:
        blockers.append("目前目錄不是 Git 工作樹，無法執行變更範圍稽核")
    if not wrapper.is_file() and system_maven is None:
        blockers.append("沒有 Maven Wrapper，系統也找不到 mvn")
    if modules and not all_frameworks:
        blockers.append("來源 pom.xml 未宣告可確認的 JUnit 或 TestNG 測試框架")
    if wrapper.is_file() and not os.access(wrapper, os.X_OK):
        warnings.append("找到 mvnw，但目前沒有可執行權限")
    if git["available"] and not git["clean"]:
        warnings.append("Git 工作樹已有變更；繼續前必須取得使用者同意")
    if custom_test_roots:
        blockers.append("偵測到自訂 testSourceDirectory；預設編輯權限不涵蓋此路徑")
    if modules and not jacoco_active:
        warnings.append("未確認 JaCoCo 已在直接建置區段宣告；第一版不得自行修改 pom.xml")
    if modules and not pit_active:
        warnings.append("未確認 PIT 已在直接建置區段宣告；第一版不得自行修改 pom.xml")

    report = {
        "status": "blocked" if blockers else "ready",
        "inspection_scope": (
            "只檢查專案內來源 pom.xml；尚未解析外部父 POM、有效設定檔、"
            "命令列屬性或 Maven 實際相依圖"
        ),
        "repo": str(repo),
        "git": git,
        "maven": {
            "wrapper": str(wrapper) if wrapper.is_file() else None,
            "wrapper_executable": wrapper.is_file() and os.access(wrapper, os.X_OK),
            "system_maven": system_maven,
        },
        "capabilities": {
            "test_frameworks": all_frameworks,
            "jacoco_configured": jacoco_active,
            "pit_configured": pit_active,
        },
        "modules": modules,
        "parse_errors": parse_errors,
        "blockers": blockers,
        "warnings": warnings,
    }
    print(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2 if args.pretty else None,
            sort_keys=True,
        )
    )
    return 2 if blockers else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
