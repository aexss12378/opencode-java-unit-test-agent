# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "tree-sitter==0.26.0",
#   "tree-sitter-java==0.23.5",
# ]
# ///

"""建立 Javadoc 執行專用的分支、worktree 與宣告清單。"""

from __future__ import annotations

import argparse
import json
import re
import sys
import uuid
from pathlib import Path

from javadoc_common import (
    JavadocError,
    declaration_to_state,
    git,
    java_files,
    save_state,
    sha256_bytes,
    target_declarations,
)


def remote_default_branch(repo: Path) -> tuple[str, str]:
    git(repo, "fetch", "--prune", "origin", message="無法更新 origin")
    reference = git(
        repo,
        "ls-remote",
        "--symref",
        "origin",
        "HEAD",
        message="無法查詢 origin 的遠端預設分支",
    )
    match = re.search(r"(?m)^ref:\s+refs/heads/(\S+)\s+HEAD$", reference)
    if match is None:
        raise JavadocError("無法辨識 origin 的遠端預設分支")
    branch = match.group(1)
    if not re.fullmatch(r"[A-Za-z0-9._/-]+", branch):
        raise JavadocError("遠端預設分支名稱不合法")
    sha = git(repo, "rev-parse", f"origin/{branch}")
    return branch, sha


def prepare(repo: Path, payload: dict[str, object]) -> dict[str, object]:
    if not (repo / ".git").exists():
        raise JavadocError("目前工作目錄不是 Git 專案根目錄")
    if not (repo / "pom.xml").is_file():
        raise JavadocError("只支援專案根目錄含 pom.xml 的 Maven 專案")

    target_path = payload.get("target_path")
    if target_path is not None and not isinstance(target_path, str):
        raise JavadocError("target_path 必須是字串")

    default_branch, base_sha = remote_default_branch(repo)
    run_id = str(uuid.uuid4())
    worktree_relative = f"javadoc-worktrees/{run_id}"
    worktree = repo / "javadoc-worktrees" / run_id
    branch = f"opencode/javadoc/{run_id}"
    worktree.parent.mkdir(parents=True, exist_ok=True)

    git(
        repo,
        "worktree",
        "add",
        "-b",
        branch,
        str(worktree),
        base_sha,
        message="無法建立 Javadoc worktree",
    )
    try:
        paths = java_files(worktree, target_path)
        files: dict[str, object] = {}
        for path in paths:
            source = (worktree / path).read_bytes()
            targets = target_declarations(source, path=path)
            files[path] = {
                "initial_sha256": sha256_bytes(source),
                "last_applied_sha256": sha256_bytes(source),
                "targets": {
                    item.key: {
                        **declaration_to_state(item),
                        "review": None,
                    }
                    for item in targets
                },
            }

        state: dict[str, object] = {
            "version": 1,
            "run_id": run_id,
            "repo": str(repo.resolve()),
            "worktree": worktree_relative,
            "branch": branch,
            "remote": "origin",
            "default_branch": default_branch,
            "base_sha": base_sha,
            "status": "prepared",
            "files": files,
            "validation": None,
            "publication": None,
        }
        save_state(repo, state)
    except Exception:
        git(
            repo,
            "worktree",
            "remove",
            "--force",
            str(worktree),
            message="Javadoc 準備失敗且無法清理 worktree",
        )
        git(repo, "branch", "-D", branch, message="Javadoc 準備失敗且無法清理分支")
        raise

    return {
        "status": "prepared",
        "run_id": run_id,
        "worktree": worktree_relative,
        "branch": branch,
        "base_branch": default_branch,
        "base_sha": base_sha,
        "files": [
            {
                "path": path,
                "targets": [
                    {
                        "key": target["key"],
                        "kind": target["kind"],
                        "name": target["name"],
                        "line": target["line"],
                        "has_javadoc": target.get("javadoc_start") is not None,
                        "required": target["required"],
                        "metadata": target["metadata"],
                    }
                    for target in file_state["targets"].values()
                ],
            }
            for path, file_state in files.items()
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True, type=Path)
    arguments = parser.parse_args()
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise JavadocError("輸入必須是 JSON 物件")
        result = prepare(arguments.repo.resolve(), payload)
    except (JavadocError, json.JSONDecodeError) as error:
        result = {"status": "rejected", "message": str(error)}
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
