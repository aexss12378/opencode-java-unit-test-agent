# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "tree-sitter==0.26.0",
#   "tree-sitter-java==0.23.5",
# ]
# ///

from __future__ import annotations

import json
import os
import re
import stat
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import tree_sitter_java
from tree_sitter import Language, Node, Parser


class GateError(Exception):
    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True)
class Addition:
    start_byte: int
    javadoc: str


@dataclass(frozen=True)
class Insertion:
    offset: int
    content: bytes


def main() -> None:
    try:
        repository = repository_from_arguments()
        request = read_request()
        result = apply(repository, request)
    except GateError as error:
        guidance = f"{error}；沒有寫入任何檔案"
        if error.retryable:
            guidance += "。請重新掃描該 Java 檔案，修正 Javadoc 內文或 start_byte 後再提交"
        result = {
            "status": "blocked",
            "message": guidance,
            "retryable": error.retryable,
            "written": False,
        }
    except Exception as error:  # noqa: BLE001
        # 工具邊界必須把未知例外轉成 Agent 能理解的結果。
        result = {
            "status": "tool-error",
            "message": f"{type(error).__name__}: {error}",
            "retryable": False,
            "written": False,
        }
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))


def repository_from_arguments() -> Path:
    if len(sys.argv) != 3 or sys.argv[1] != "--repo":
        raise GateError("後端參數必須是 --repo <目前工作目錄>")
    try:
        repository = Path(sys.argv[2]).resolve(strict=True)
        current = Path.cwd().resolve(strict=True)
    except OSError as error:
        raise GateError(f"無法確認工作目錄：{error}") from error
    if repository != current:
        raise GateError("--repo 必須指向目前工作目錄")
    return repository


def read_request() -> tuple[str, list[Addition]]:
    try:
        value = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise GateError("輸入不是有效 JSON") from error
    if not isinstance(value, dict):
        raise GateError("輸入必須是 JSON 物件")

    raw_path = value.get("path")
    raw_additions = value.get("additions")
    if not isinstance(raw_path, str) or not raw_path:
        raise GateError("缺少 Java 檔案路徑")
    if not isinstance(raw_additions, list) or not raw_additions:
        raise GateError("至少需要一筆 Javadoc 新增資料")

    additions: list[Addition] = []
    for item in raw_additions:
        if not isinstance(item, dict):
            raise GateError("Javadoc 新增資料必須是物件")
        start_byte = item.get("start_byte")
        javadoc = item.get("javadoc")
        if isinstance(start_byte, bool) or not isinstance(start_byte, int):
            raise GateError("start_byte 必須是整數")
        if start_byte < 0:
            raise GateError("start_byte 不得小於 0")
        if not isinstance(javadoc, str):
            raise GateError("Javadoc 內文必須是字串")
        additions.append(Addition(start_byte, normalize_javadoc(javadoc)))
    return raw_path, additions


def apply(
    repository: Path,
    request: tuple[str, list[Addition]],
) -> dict[str, Any]:
    raw_path, additions = request
    target = source_file(repository, raw_path)
    before_bytes = target.read_bytes()
    try:
        before_bytes.decode("utf-8")
    except UnicodeDecodeError as error:
        raise GateError("Java 檔案不是有效 UTF-8") from error

    parser = Parser(Language(tree_sitter_java.language()))
    before_tree = parse_java(parser, before_bytes)
    declarations = declarations_by_start_byte(before_tree.root_node)
    newline = b"\r\n" if b"\r\n" in before_bytes else b"\n"
    requested_start_bytes: set[int] = set()
    insertions: list[Insertion] = []

    for addition in additions:
        if addition.start_byte in requested_start_bytes:
            raise GateError(
                f"start_byte {addition.start_byte} 被重複提交",
                retryable=True,
            )
        requested_start_bytes.add(addition.start_byte)

        matches = declarations.get(addition.start_byte, [])
        if len(matches) != 1:
            raise GateError(
                f"start_byte {addition.start_byte} 不是唯一且可新增 Javadoc 的 Java 宣告",
                retryable=True,
            )
        declaration = matches[0]
        if has_javadoc(declaration, before_bytes):
            raise GateError(
                f"start_byte {addition.start_byte} 的宣告已經有 Javadoc",
                retryable=True,
            )

        indentation_start = declaration.start_byte - declaration.start_point.column
        indentation = before_bytes[indentation_start : declaration.start_byte]
        if any(value not in (ord(" "), ord("\t")) for value in indentation):
            raise GateError(
                f"start_byte {addition.start_byte} 的宣告不是獨立行的起始內容",
            )
        insertions.append(
            Insertion(
                declaration.start_byte,
                render_javadoc(indentation, addition.javadoc, newline),
            )
        )

    candidate = bytearray(before_bytes)
    for insertion in sorted(insertions, key=lambda item: item.offset, reverse=True):
        candidate[insertion.offset : insertion.offset] = insertion.content
    after_bytes = bytes(candidate)

    after_tree = parse_java(parser, after_bytes)
    before_count = count_javadocs(before_tree.root_node, before_bytes)
    after_count = count_javadocs(after_tree.root_node, after_bytes)
    if after_count != before_count + len(insertions):
        raise GateError(
            "新增內容沒有全部被解析為 Javadoc 註解",
            retryable=True,
        )

    if target.read_bytes() != before_bytes:
        raise GateError(
            "Java 檔案在驗證期間已被其他程序修改",
            retryable=True,
        )
    atomic_write(target, before_bytes, after_bytes)
    return {
        "status": "published",
        "path": raw_path,
        "added": len(additions),
        "start_bytes": sorted(requested_start_bytes),
        "written": True,
    }


def source_file(repository: Path, raw_path: str) -> Path:
    if "\\" in raw_path:
        raise GateError("只能使用專案相對的正斜線路徑")
    relative = PurePosixPath(raw_path)
    parts = relative.parts
    if (
        relative.is_absolute()
        or parts[:3] != ("src", "main", "java")
        or relative.suffix != ".java"
        or any(part in ("", ".", "..") for part in parts)
    ):
        raise GateError("只能修改 src/main/java/** 下的既有 .java 檔案")

    current = repository
    for part in parts:
        current /= part
        if current.is_symlink():
            raise GateError("Java 檔案路徑不得包含符號連結")
    if not current.is_file():
        raise GateError("目標必須是既有 Java 一般檔案")

    source_root = (repository / "src/main/java").resolve(strict=True)
    target = current.resolve(strict=True)
    try:
        target.relative_to(source_root)
    except ValueError as error:
        raise GateError("Java 檔案真實路徑離開 src/main/java") from error
    return target


def parse_java(parser: Parser, source: bytes):
    tree = parser.parse(source)
    if tree.root_node.has_error:
        raise GateError("Java 原始碼包含無法解析的語法")
    return tree


def walk(node: Node):
    yield node
    for child in node.named_children:
        yield from walk(child)


def declarations_by_start_byte(root: Node) -> dict[int, list[Node]]:
    declarations: dict[int, list[Node]] = {}
    for node in walk(root):
        if is_documentable_declaration(node):
            declarations.setdefault(node.start_byte, []).append(node)
    return declarations


def is_documentable_declaration(node: Node) -> bool:
    parent = node.parent
    if parent is None:
        return False
    if node.type == "enum_constant":
        return parent.type == "enum_body"
    if not node.type.endswith("_declaration"):
        return False
    if parent.type == "program":
        return node.type not in (
            "import_declaration",
            "module_declaration",
            "package_declaration",
        )
    return parent.type in (
        "annotation_type_body",
        "class_body",
        "enum_body_declarations",
        "interface_body",
    )


def has_javadoc(declaration: Node, source: bytes) -> bool:
    previous = declaration.prev_named_sibling
    while previous is not None and previous.type.endswith("_comment"):
        if previous.type == "block_comment" and source[
            previous.start_byte : previous.end_byte
        ].startswith(b"/**"):
            return True
        previous = previous.prev_named_sibling
    return False


def count_javadocs(root: Node, source: bytes) -> int:
    return sum(
        1
        for node in walk(root)
        if node.type == "block_comment"
        and source[node.start_byte : node.end_byte].startswith(b"/**")
    )


def normalize_javadoc(value: str) -> str:
    normalized = value.replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    if not normalized.strip():
        raise GateError("Javadoc 內文不得為空", retryable=True)
    if "\x00" in normalized or "*/" in normalized or re.search(r"\\u+", normalized):
        raise GateError(
            "Javadoc 內文包含可能提早結束註解並改動 Java 程式碼的內容",
            retryable=True,
        )
    try:
        normalized.encode("utf-8")
    except UnicodeEncodeError as error:
        raise GateError("Javadoc 內文不是有效 UTF-8", retryable=True) from error
    return normalized


def render_javadoc(indentation: bytes, body: str, newline: bytes) -> bytes:
    rendered = bytearray(b"/**" + newline)
    for line in body.split("\n"):
        rendered.extend(indentation + b" *")
        if line:
            rendered.extend(b" " + line.encode("utf-8"))
        rendered.extend(newline)
    rendered.extend(indentation + b" */" + newline + indentation)
    return bytes(rendered)


def atomic_write(target: Path, before: bytes, after: bytes) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        dir=target.parent,
        prefix=f".{target.name}.javadoc-edit-",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(after)
            stream.flush()
            os.fsync(stream.fileno())
        original_mode = stat.S_IMODE(os.stat(target, follow_symlinks=False).st_mode)
        os.chmod(temporary, original_mode)
        if target.is_symlink() or target.read_bytes() != before:
            raise GateError(
                "Java 檔案在寫入前已被其他程序修改",
                retryable=True,
            )
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
