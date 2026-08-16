# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "tree-sitter==0.26.0",
#   "tree-sitter-java==0.23.5",
# ]
# ///

"""對單一 Java 檔案原子新增或整段取代一批 Javadoc。"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import tree_sitter_java
from tree_sitter import Language, Node, Parser

DECLARATIONS = {
    "annotation_type_declaration",
    "annotation_type_element_declaration",
    "class_declaration",
    "compact_constructor_declaration",
    "constant_declaration",
    "constructor_declaration",
    "enum_declaration",
    "field_declaration",
    "interface_declaration",
    "method_declaration",
    "record_declaration",
}


class ApplyError(RuntimeError):
    pass


@dataclass(frozen=True)
class Declaration:
    kind: str
    name: str
    line: int
    start: int
    javadoc_start: int | None
    javadoc_end: int | None

    @property
    def has_javadoc(self) -> bool:
        return self.javadoc_start is not None


def git(repo: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *arguments],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )
    if result.returncode != 0:
        raise ApplyError((result.stdout + result.stderr).strip()[-4_000:])
    return result.stdout.strip()


def normalize_path(value: str) -> str:
    raw = value.strip().removeprefix("@")
    relative = PurePosixPath(raw)
    if (
        "\\" in raw
        or relative.is_absolute()
        or any(part in ("", ".", "..") for part in relative.parts)
    ):
        raise ApplyError("必須使用專案相對的正斜線路徑")
    return relative.as_posix()


def load_state(repo: Path, worktree: str) -> dict[str, Any]:
    relative = normalize_path(worktree)
    parts = PurePosixPath(relative).parts
    if len(parts) != 2 or parts[0] != "javadoc-worktrees":
        raise ApplyError("worktree 格式不正確")
    common = Path(git(repo, "rev-parse", "--git-common-dir"))
    if not common.is_absolute():
        common = repo / common
    state_file = common.resolve() / "opencode-javadoc" / f"{parts[1]}.json"
    if not state_file.is_file():
        raise ApplyError("找不到 Javadoc 執行狀態")
    state = json.loads(state_file.read_text(encoding="utf-8"))
    if state.get("worktree") != relative:
        raise ApplyError("執行狀態與 worktree 不一致")
    return state


def primary_worktree(repo: Path) -> Path:
    common = Path(git(repo, "rev-parse", "--git-common-dir"))
    if not common.is_absolute():
        common = repo / common
    common = common.resolve()
    if common.name != ".git" or not common.is_dir():
        raise ApplyError("無法辨識主要 Git worktree")
    return common.parent


def resolve_worktree(repo: Path, relative: str) -> Path:
    root = primary_worktree(repo)
    worktree = root.joinpath(*PurePosixPath(relative).parts).resolve()
    if worktree.parent != (root / "javadoc-worktrees").resolve() or not worktree.is_dir():
        raise ApplyError("Javadoc worktree 不存在或路徑不合法")
    execution_root = Path(git(repo, "rev-parse", "--show-toplevel")).resolve()
    if execution_root not in (root.resolve(), worktree):
        raise ApplyError("寫入工具不是從主要 worktree 或目標 Javadoc worktree 執行")
    return worktree


def git_blob(repo: Path, revision: str, path: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(repo), "show", f"{revision}:{path}"],
        cwd=repo,
        capture_output=True,
        timeout=120,
        check=False,
    )
    if result.returncode != 0:
        raise ApplyError(f"無法讀取基準檔案：{path}")
    return result.stdout


def walk(node: Node) -> Iterable[Node]:
    yield node
    for child in node.named_children:
        yield from walk(child)


def text(node: Node | None, source: bytes) -> str:
    return "" if node is None else source[node.start_byte : node.end_byte].decode()


def nearest_type(node: Node) -> Node | None:
    current = node.parent
    while current is not None:
        if current.type in {
            "annotation_type_declaration",
            "class_declaration",
            "enum_declaration",
            "interface_declaration",
            "record_declaration",
        }:
            return current
        current = current.parent
    return None


def declaration_name(node: Node, source: bytes) -> str:
    name = node.child_by_field_name("name")
    if name is not None:
        return text(name, source)
    if node.type in ("field_declaration", "constant_declaration"):
        names = [
            text(item.child_by_field_name("name"), source)
            for item in walk(node)
            if item.type == "variable_declarator"
            and item.child_by_field_name("name") is not None
        ]
        if names:
            return ",".join(names)
    if node.type == "compact_constructor_declaration":
        parent = nearest_type(node)
        if parent is not None:
            return declaration_name(parent, source)
    value = text(node, source)
    if node.type == "package_declaration":
        return re.sub(r"^\s*package\s+|\s*;\s*$", "", value)
    if node.type == "module_declaration":
        match = re.search(r"\bmodule\s+([\w.]+)", value)
        return match.group(1) if match else "module"
    return node.type


def declaration_line(node: Node) -> int:
    name = node.child_by_field_name("name")
    if name is not None:
        return name.start_point.row + 1
    if node.type in ("field_declaration", "constant_declaration"):
        for item in walk(node):
            if item.type == "variable_declarator":
                name = item.child_by_field_name("name")
                if name is not None:
                    return name.start_point.row + 1
    return node.start_point.row + 1


def scan(source: bytes, path: str) -> list[Declaration]:
    try:
        source.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ApplyError(f"Java 檔案不是 UTF-8：{path}") from error
    root = Parser(Language(tree_sitter_java.language())).parse(source).root_node
    if root.has_error:
        raise ApplyError(f"Java 原始碼無法完整解析：{path}")
    filename = PurePosixPath(path).name
    declarations = []
    for node in walk(root):
        documentable = node.type in DECLARATIONS
        documentable |= (
            node.type == "enum_constant"
            and node.parent is not None
            and node.parent.type == "enum_body"
        )
        documentable |= node.type == "package_declaration" and filename == "package-info.java"
        documentable |= node.type == "module_declaration" and filename == "module-info.java"
        if not documentable:
            continue
        previous = node.prev_named_sibling
        javadoc = None
        if previous is not None and previous.type == "block_comment":
            raw = source[previous.start_byte : previous.end_byte]
            gap = source[previous.end_byte : node.start_byte]
            if raw.startswith(b"/**") and not gap.strip():
                javadoc = (previous.start_byte, previous.end_byte)
        declarations.append(
            Declaration(
                kind=node.type,
                name=declaration_name(node, source),
                line=declaration_line(node),
                start=node.start_byte,
                javadoc_start=javadoc[0] if javadoc else None,
                javadoc_end=javadoc[1] if javadoc else None,
            )
        )
    return declarations


def without_javadocs(source: bytes, path: str) -> bytes:
    ranges = {
        (item.javadoc_start, item.start)
        for item in scan(source, path)
        if item.has_javadoc
    }
    result = source
    for start, end in sorted(ranges, reverse=True):
        assert start is not None
        result = result[:start] + result[end:]
    return result


def normalize_javadoc(value: str) -> str:
    body = value.replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    if not body.strip():
        raise ApplyError("Javadoc 內文不得為空")
    if "/**" in body or "*/" in body:
        raise ApplyError("Javadoc 只能提供內文，不得包含註解邊界")
    if any(re.match(r"^[ \t]*\*", line) for line in body.split("\n")):
        raise ApplyError("Javadoc 內文每行不得自行包含開頭星號")
    if "\x00" in body or re.search(r"\\u+", body):
        raise ApplyError("Javadoc 內文包含不安全字元")
    return body


def indentation_at(source: bytes, offset: int) -> bytes:
    indentation = source[source.rfind(b"\n", 0, offset) + 1 : offset]
    if indentation.strip(b" \t"):
        raise ApplyError("宣告前含非縮排字元，無法安全寫入")
    return indentation


def render(indentation: bytes, body: str, newline: bytes) -> bytes:
    lines = [b"/**"]
    lines.extend(
        indentation + b" *" + (b" " + line.encode() if line else b"")
        for line in body.split("\n")
    )
    lines.append(indentation + b" */")
    return newline.join(lines)


def apply(repo: Path, payload: dict[str, Any]) -> dict[str, Any]:
    worktree_name, path_value, changes = (
        payload.get("worktree"),
        payload.get("path"),
        payload.get("changes"),
    )
    if not isinstance(worktree_name, str) or not isinstance(path_value, str):
        raise ApplyError("worktree 與 path 必須是字串")
    if not isinstance(changes, list) or not changes:
        raise ApplyError("changes 必須是非空陣列")
    state = load_state(repo, worktree_name)
    path = normalize_path(path_value)
    if state.get("status") != "prepared" or path not in state["files"]:
        raise ApplyError("此檔案不在可寫入範圍")
    worktree = resolve_worktree(repo, worktree_name)
    source_path = worktree / path
    if source_path.is_symlink() or not source_path.is_file():
        raise ApplyError("目標 Java 檔案不存在或是符號連結")
    source = source_path.read_bytes()
    if without_javadocs(source, path) != without_javadocs(
        git_blob(worktree, state["base_sha"], path), path
    ):
        raise ApplyError("檔案含 Javadoc 以外的變更")

    declarations = scan(source, path)
    newline = b"\r\n" if b"\r\n" in source else b"\n"
    edits = []
    identities = set()
    for change in changes:
        if not isinstance(change, dict):
            raise ApplyError("每個 change 都必須是物件")
        line, name, body = change.get("line"), change.get("name"), change.get("javadoc")
        if not isinstance(line, int) or not isinstance(name, str) or not isinstance(body, str):
            raise ApplyError("每個 change 都必須提供 line、name 與 javadoc")
        if (line, name) in identities:
            raise ApplyError("同一批次不得重複指定同一宣告")
        identities.add((line, name))
        matches = [item for item in declarations if item.line == line and item.name == name]
        if len(matches) != 1:
            raise ApplyError(f"無法唯一找到第 {line} 行的宣告 {name}")
        declaration = matches[0]
        anchor = declaration.javadoc_start if declaration.has_javadoc else declaration.start
        assert anchor is not None
        indentation = indentation_at(source, anchor)
        replacement = render(indentation, normalize_javadoc(body), newline)
        if declaration.has_javadoc:
            assert declaration.javadoc_end is not None
            edits.append((anchor, declaration.javadoc_end, replacement))
        else:
            edits.append((declaration.start, declaration.start, replacement + newline + indentation))

    updated = source
    for start, end, replacement in sorted(edits, reverse=True):
        updated = updated[:start] + replacement + updated[end:]
    current = scan(updated, path)
    descriptor, temporary_name = tempfile.mkstemp(dir=source_path.parent)
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
    return {
        "status": "applied",
        "path": path,
        "changed": updated != source,
        "declarations": [
            {
                "kind": item.kind,
                "name": item.name,
                "line": item.line,
                "has_javadoc": item.has_javadoc,
            }
            for item in current
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True, type=Path)
    arguments = parser.parse_args()
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise ApplyError("輸入必須是 JSON 物件")
        result = apply(arguments.repo.resolve(), payload)
    except (ApplyError, json.JSONDecodeError, OSError) as error:
        result = {"status": "rejected", "message": str(error)}
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
