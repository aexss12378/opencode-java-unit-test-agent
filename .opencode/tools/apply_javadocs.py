# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "tree-sitter==0.26.0",
#   "tree-sitter-java==0.23.5",
# ]
# ///

"""對單一 Java 檔案原子套用一批 Javadoc 審查決策。"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from javadoc_common import (
    JavadocError,
    locked_state,
    normalize_javadoc,
    normalize_relative_path,
    render_javadoc,
    resolve_worktree,
    save_state,
    scan_declarations,
    sha256_bytes,
    validate_javadoc_body,
)


def indentation_at(source: bytes, offset: int) -> bytes:
    line_start = source.rfind(b"\n", 0, offset) + 1
    indentation = source[line_start:offset]
    if indentation.strip(b" \t"):
        raise JavadocError("宣告前含非縮排字元，無法安全寫入")
    return indentation


def newline_for(source: bytes) -> bytes:
    return b"\r\n" if b"\r\n" in source else b"\n"


def apply(repo: Path, payload: dict[str, Any]) -> dict[str, Any]:
    worktree_name = payload.get("worktree")
    path_value = payload.get("path")
    reviews = payload.get("reviews")
    if not isinstance(worktree_name, str) or not isinstance(path_value, str):
        raise JavadocError("worktree 與 path 必須是字串")
    if not isinstance(reviews, list) or not reviews:
        raise JavadocError("reviews 必須是非空陣列")
    path = normalize_relative_path(path_value)
    worktree = resolve_worktree(repo, worktree_name)

    with locked_state(repo, worktree_name) as state:
        if state.get("status") not in ("prepared", "editing"):
            raise JavadocError("目前執行狀態不可再寫入 Javadoc")
        file_state = state.get("files", {}).get(path)
        if not isinstance(file_state, dict):
            raise JavadocError("此檔案不在本次 Javadoc 執行範圍")
        target_states = file_state.get("targets", {})
        source_path = worktree / path
        if source_path.is_symlink() or not source_path.is_file():
            raise JavadocError("目標 Java 檔案不存在或是符號連結")
        source = source_path.read_bytes()
        if sha256_bytes(source) != file_state.get("last_applied_sha256"):
            raise JavadocError("檔案含未經 apply_javadocs 記錄的變更")

        current = {item.key: item for item in scan_declarations(source, path=path)}
        seen: set[str] = set()
        edits: list[tuple[int, int, bytes, str, dict[str, Any]]] = []
        decisions: list[tuple[str, dict[str, Any]]] = []
        newline = newline_for(source)

        for review in reviews:
            if not isinstance(review, dict):
                raise JavadocError("每個 review 都必須是物件")
            key = review.get("key")
            decision = review.get("decision")
            if not isinstance(key, str) or key not in target_states:
                raise JavadocError("review 指定了未知宣告 key")
            if key in seen:
                raise JavadocError("同一批次不得重複審查同一宣告")
            seen.add(key)
            if target_states[key].get("review") is not None:
                raise JavadocError("此宣告已完成審查")
            declaration = current.get(key)
            if declaration is None:
                raise JavadocError("找不到目前版本的目標宣告")
            if decision not in ("write", "skip", "blocked"):
                raise JavadocError("decision 只能是 write、skip 或 blocked")

            reason = review.get("reason")
            if decision == "write":
                body_value = review.get("javadoc")
                if not isinstance(body_value, str):
                    raise JavadocError("write 決策必須提供 javadoc")
                body = normalize_javadoc(body_value)
                validate_javadoc_body(body, declaration)
                indentation = indentation_at(
                    source,
                    declaration.javadoc_start
                    if declaration.has_javadoc
                    else declaration.start_byte,
                )
                rendered = render_javadoc(indentation, body, newline)
                if declaration.has_javadoc:
                    start = declaration.javadoc_start
                    end = declaration.javadoc_end
                    assert start is not None and end is not None
                    replacement = rendered
                else:
                    start = declaration.start_byte
                    end = declaration.start_byte
                    replacement = rendered + newline + indentation
                edits.append((start, end, replacement, key, {"decision": decision}))
                decisions.append((key, {"decision": decision}))
            elif decision == "skip":
                if declaration.required and not declaration.has_javadoc:
                    raise JavadocError("必要宣告缺少 Javadoc 時不可使用 skip")
                decisions.append(
                    (
                        key,
                        {
                            "decision": decision,
                            "reason": str(reason or "既有 Javadoc 仍正確"),
                        },
                    )
                )
            else:
                if not isinstance(reason, str) or not reason.strip():
                    raise JavadocError("blocked 決策必須說明規格與原始碼衝突")
                decisions.append(
                    (key, {"decision": decision, "reason": reason.strip()})
                )

        updated = source
        for start, end, replacement, _key, _review in sorted(edits, reverse=True):
            updated = updated[:start] + replacement + updated[end:]
        scan_declarations(updated, path=path)

        if updated != source:
            descriptor, temporary_name = tempfile.mkstemp(
                dir=source_path.parent, prefix=f".{source_path.name}."
            )
            temporary = Path(temporary_name)
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    os.fchmod(stream.fileno(), source_path.stat().st_mode & 0o777)
                    stream.write(updated)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, source_path)
            finally:
                temporary.unlink(missing_ok=True)

        rescanned = {item.key: item for item in scan_declarations(updated, path=path)}
        for key, review_value in decisions:
            target_states[key]["review"] = review_value
        for key, target in target_states.items():
            if key in rescanned:
                declaration = rescanned[key]
                target["line"] = declaration.line
                target["start_byte"] = declaration.start_byte
                target["end_byte"] = declaration.end_byte
                target["javadoc_start"] = declaration.javadoc_start
                target["javadoc_end"] = declaration.javadoc_end
        file_state["last_applied_sha256"] = sha256_bytes(updated)
        state["status"] = "editing"
        save_state(repo, state)

        remaining = [
            {
                "key": key,
                "kind": value["kind"],
                "name": value["name"],
                "line": value["line"],
                "has_javadoc": value.get("javadoc_start") is not None,
                "required": value["required"],
                "metadata": value.get("metadata", {}),
            }
            for key, value in target_states.items()
            if value.get("review") is None
        ]
        return {
            "status": "applied",
            "path": path,
            "reviewed": len(decisions),
            "changed": updated != source,
            "remaining": remaining,
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True, type=Path)
    arguments = parser.parse_args()
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise JavadocError("輸入必須是 JSON 物件")
        result = apply(arguments.repo.resolve(), payload)
    except (JavadocError, json.JSONDecodeError, OSError) as error:
        result = {"status": "rejected", "message": str(error)}
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
