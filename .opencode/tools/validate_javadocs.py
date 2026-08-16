# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "tree-sitter==0.26.0",
#   "tree-sitter-java==0.23.5",
# ]
# ///

"""排除未完成檔案，驗證 Javadoc-only 差異與 Maven 編譯。"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from javadoc_common import (
    JavadocError,
    checked_command,
    git,
    locked_state,
    resolve_worktree,
    save_state,
    scan_declarations,
    sha256_bytes,
    without_attached_javadocs,
)


def has_javadoc_plugin(worktree: Path) -> bool:
    for pom in worktree.rglob("pom.xml"):
        if any(part in ("target", ".git", "javadoc-worktrees") for part in pom.parts):
            continue
        try:
            root = ET.parse(pom).getroot()
        except ET.ParseError as error:
            raise JavadocError(f"pom.xml 無法解析：{pom}") from error
        for node in root.iter():
            if (
                node.tag.rsplit("}", 1)[-1] == "artifactId"
                and (node.text or "").strip() == "maven-javadoc-plugin"
            ):
                return True
    return False


def maven_command(worktree: Path) -> list[str]:
    wrapper = worktree / "mvnw"
    if wrapper.is_file() and os.access(wrapper, os.X_OK):
        return ["./mvnw"]
    return ["mvn"]


def original_bytes(worktree: Path, revision: str, path: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(worktree), "show", f"{revision}:{path}"],
        cwd=worktree,
        capture_output=True,
        timeout=120,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace")[-4000:]
        raise JavadocError(f"無法讀取基準檔案：{path}：{detail}")
    return result.stdout


def validate(repo: Path, payload: dict[str, Any]) -> dict[str, Any]:
    worktree_name = payload.get("worktree")
    results = payload.get("file_results")
    if not isinstance(worktree_name, str) or not isinstance(results, list):
        raise JavadocError("worktree 必須是字串，file_results 必須是陣列")
    worktree = resolve_worktree(repo, worktree_name)

    with locked_state(repo, worktree_name) as state:
        if state.get("status") not in ("prepared", "editing"):
            raise JavadocError("目前執行狀態不可驗證")
        expected = set(state.get("files", {}))
        outcomes: dict[str, dict[str, str]] = {}
        for item in results:
            if not isinstance(item, dict):
                raise JavadocError("每個 file_result 都必須是物件")
            path = item.get("path")
            status = item.get("status")
            message = item.get("message", "")
            if not isinstance(path, str) or path not in expected:
                raise JavadocError("file_results 包含本次範圍外的路徑")
            if path in outcomes:
                raise JavadocError("file_results 不得重複路徑")
            if status not in ("completed", "failed"):
                raise JavadocError("檔案狀態只能是 completed 或 failed")
            outcomes[path] = {"status": status, "message": str(message)}
        if set(outcomes) != expected:
            missing = sorted(expected - set(outcomes))
            raise JavadocError(f"尚未回報所有檔案結果：{missing[:10]}")

        failed: list[dict[str, str]] = []
        for path, outcome in outcomes.items():
            file_state = state["files"][path]
            if outcome["status"] == "failed":
                git(
                    worktree,
                    "restore",
                    "--source",
                    state["base_sha"],
                    "--",
                    path,
                    message=f"無法還原失敗檔案：{path}",
                )
                restored = (worktree / path).read_bytes()
                file_state["last_applied_sha256"] = sha256_bytes(restored)
                file_state["outcome"] = "failed"
                failed.append(
                    {"path": path, "reason": outcome["message"] or "逐檔子代理未完成"}
                )
                continue
            incomplete = [
                key
                for key, target in file_state["targets"].items()
                if target.get("review") is None
            ]
            if incomplete:
                raise JavadocError(
                    f"{path} 尚有 {len(incomplete)} 個宣告未審查，不可標示完成"
                )
            current = (worktree / path).read_bytes()
            if sha256_bytes(current) != file_state.get("last_applied_sha256"):
                raise JavadocError(f"{path} 含未經 apply_javadocs 記錄的變更")
            file_state["outcome"] = "completed"

        changed_output = git(worktree, "diff", "--name-only", state["base_sha"], "--")
        changed = [line for line in changed_output.splitlines() if line]
        completed = {
            path for path, result in outcomes.items() if result["status"] == "completed"
        }
        unexpected = [path for path in changed if path not in completed]
        if unexpected:
            raise JavadocError(f"發現範圍外變更：{unexpected[:10]}")

        for path in changed:
            original = original_bytes(worktree, state["base_sha"], path)
            current = (worktree / path).read_bytes()
            scan_declarations(current, path=path)
            if without_attached_javadocs(
                original, path=path
            ) != without_attached_javadocs(current, path=path):
                raise JavadocError(f"{path} 含 Javadoc 以外的變更")
        git(
            worktree,
            "diff",
            "--check",
            state["base_sha"],
            "--",
            *changed,
            message="Git 差異格式檢查失敗",
        )

        commands: list[dict[str, str]] = []
        command = maven_command(worktree)
        compile_result = checked_command(
            [*command, "-B", "-ntp", "-DskipTests", "compile"],
            cwd=worktree,
            timeout=900,
            message="Maven 編譯失敗",
        )
        commands.append(
            {
                "name": "compile",
                "command": " ".join([*command, "-B", "-ntp", "-DskipTests", "compile"]),
                "output": compile_result[-2000:],
            }
        )
        if has_javadoc_plugin(worktree):
            docs_result = checked_command(
                [*command, "-B", "-ntp", "-DskipTests", "javadoc:javadoc"],
                cwd=worktree,
                timeout=900,
                message="Maven Javadoc 檢查失敗",
            )
            commands.append(
                {
                    "name": "javadoc",
                    "command": " ".join(
                        [*command, "-B", "-ntp", "-DskipTests", "javadoc:javadoc"]
                    ),
                    "output": docs_result[-2000:],
                }
            )

        blocked = [
            {"path": path, "name": target["name"], "reason": target["review"]["reason"]}
            for path, file_state in state["files"].items()
            if file_state.get("outcome") == "completed"
            for target in file_state["targets"].values()
            if target.get("review", {}).get("decision") == "blocked"
        ]
        validation = {
            "changed_files": changed,
            "failed_files": failed,
            "blocked_declarations": blocked,
            "commands": commands,
        }
        state["validation"] = validation
        state["status"] = "validated"
        save_state(repo, state)
        return {"status": "validated", **validation}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True, type=Path)
    arguments = parser.parse_args()
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise JavadocError("輸入必須是 JSON 物件")
        result = validate(arguments.repo.resolve(), payload)
    except (JavadocError, json.JSONDecodeError, OSError) as error:
        result = {"status": "rejected", "message": str(error)}
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
