"""Javadoc Agent 共用的 Maven、Git 與 Tree-sitter 基礎功能。"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import tree_sitter_java
from tree_sitter import Language, Node, Parser

GIT_TIMEOUT_SECONDS = 120
TYPE_NODES = {
    "annotation_type_declaration",
    "class_declaration",
    "enum_declaration",
    "interface_declaration",
    "record_declaration",
}
MEMBER_NODES = {
    "annotation_type_element_declaration",
    "compact_constructor_declaration",
    "constant_declaration",
    "constructor_declaration",
    "field_declaration",
    "method_declaration",
}


class JavadocError(RuntimeError):
    """可安全回傳給 Agent 的輸入、狀態或環境錯誤。"""


@dataclass(frozen=True)
class Declaration:
    key: str
    kind: str
    name: str
    line: int
    start_byte: int
    end_byte: int
    javadoc_start: int | None
    javadoc_end: int | None
    required: bool
    metadata: dict[str, Any]

    @property
    def has_javadoc(self) -> bool:
        return self.javadoc_start is not None

    def summary(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "kind": self.kind,
            "name": self.name,
            "line": self.line,
            "has_javadoc": self.has_javadoc,
            "required": self.required,
            "parameters": self.metadata.get("parameters", []),
            "type_parameters": self.metadata.get("type_parameters", []),
            "record_components": self.metadata.get("record_components", []),
        }


def run_command(
    command: list[str],
    *,
    cwd: Path,
    timeout: int = GIT_TIMEOUT_SECONDS,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
            check=False,
        )
    except FileNotFoundError as error:
        raise JavadocError(f"找不到必要指令：{command[0]}") from error
    except subprocess.TimeoutExpired as error:
        raise JavadocError(f"指令逾時：{' '.join(command)}") from error


def checked_command(
    command: list[str],
    *,
    cwd: Path,
    timeout: int = GIT_TIMEOUT_SECONDS,
    message: str,
    env: dict[str, str] | None = None,
) -> str:
    result = run_command(command, cwd=cwd, timeout=timeout, env=env)
    if result.returncode != 0:
        detail = (result.stdout + result.stderr).strip()[-4_000:]
        raise JavadocError(message + (f"：{detail}" if detail else ""))
    return result.stdout.strip()


def git(repo: Path, *arguments: str, message: str = "Git 指令失敗") -> str:
    return checked_command(
        ["git", "-C", str(repo), *arguments], cwd=repo, message=message
    )


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def parser() -> Parser:
    return Parser(Language(tree_sitter_java.language()))


def parse_java(source: bytes, *, path: str) -> Node:
    tree = parser().parse(source)
    if tree.root_node.has_error:
        raise JavadocError(f"Java 原始碼無法完整解析：{path}")
    return tree.root_node


def walk(node: Node) -> Iterable[Node]:
    yield node
    for child in node.named_children:
        yield from walk(child)


def node_text(node: Node | None, source: bytes) -> str:
    if node is None:
        return ""
    return source[node.start_byte : node.end_byte].decode("utf-8")


def modifiers_text(node: Node, source: bytes) -> str:
    for child in node.named_children:
        if child.type == "modifiers":
            return node_text(child, source)
    return ""


def nearest_type(node: Node) -> Node | None:
    current = node.parent
    while current is not None:
        if current.type in TYPE_NODES:
            return current
        current = current.parent
    return None


def declaration_name(node: Node, source: bytes) -> str:
    name = node.child_by_field_name("name")
    if name is not None:
        return node_text(name, source)
    if node.type in ("field_declaration", "constant_declaration"):
        names = []
        for child in walk(node):
            if child.type == "variable_declarator":
                item = child.child_by_field_name("name")
                if item is not None:
                    names.append(node_text(item, source))
        if names:
            return ",".join(names)
    if node.type == "compact_constructor_declaration":
        parent = nearest_type(node)
        if parent is not None:
            return declaration_name(parent, source)
    if node.type == "package_declaration":
        value = node_text(node, source)
        return re.sub(r"^\s*package\s+|\s*;\s*$", "", value)
    if node.type == "module_declaration":
        value = node_text(node, source)
        match = re.search(r"\bmodule\s+([\w.]+)", value)
        return match.group(1) if match else "module"
    return node.type


def parent_type_path(node: Node, source: bytes) -> list[str]:
    values: list[str] = []
    current = node.parent
    while current is not None:
        if current.type in TYPE_NODES:
            values.append(declaration_name(current, source))
        current = current.parent
    return list(reversed(values))


def parameter_names(node: Node, source: bytes) -> list[str]:
    parameters = node.child_by_field_name("parameters")
    if parameters is None:
        return []
    names: list[str] = []
    for child in walk(parameters):
        if child.type not in ("formal_parameter", "spread_parameter"):
            continue
        name = child.child_by_field_name("name")
        if name is not None:
            value = node_text(name, source)
            if value not in names:
                names.append(value)
    return names


def type_parameter_names(node: Node, source: bytes) -> list[str]:
    parameters = node.child_by_field_name("type_parameters")
    if parameters is None:
        return []
    names: list[str] = []
    for child in walk(parameters):
        if child.type != "type_parameter":
            continue
        name = child.child_by_field_name("name")
        if name is None:
            for candidate in child.named_children:
                if candidate.type in ("type_identifier", "identifier"):
                    name = candidate
                    break
        if name is not None:
            value = node_text(name, source)
            if value not in names:
                names.append(value)
    return names


def record_component_names(node: Node, source: bytes) -> list[str]:
    if node.type != "record_declaration":
        return []
    return parameter_names(node, source)


def thrown_types(node: Node, source: bytes) -> list[str]:
    throws = node.child_by_field_name("throws")
    if throws is None:
        for child in node.named_children:
            if child.type == "throws":
                throws = child
                break
    if throws is None:
        return []
    value = re.sub(r"^\s*throws\s+", "", node_text(throws, source))
    return [part.strip() for part in value.split(",") if part.strip()]


def return_type(node: Node, source: bytes) -> str:
    if node.type != "method_declaration":
        return ""
    return node_text(node.child_by_field_name("type"), source).strip()


def find_javadoc(node: Node, source: bytes) -> tuple[int, int] | None:
    previous = node.prev_named_sibling
    if previous is None or previous.type != "block_comment":
        return None
    raw = source[previous.start_byte : previous.end_byte]
    gap = source[previous.end_byte : node.start_byte]
    if raw.startswith(b"/**") and not gap.strip():
        return previous.start_byte, previous.end_byte
    return None


def is_visible_type(node: Node, source: bytes) -> bool:
    modifiers = modifiers_text(node, source)
    parent_type = nearest_type(node)
    if parent_type is None:
        return bool(re.search(r"\bpublic\b", modifiers))
    parent = node.parent
    implicit_public = parent is not None and parent.type in (
        "annotation_type_body",
        "interface_body",
    )
    visible = implicit_public or bool(re.search(r"\b(public|protected)\b", modifiers))
    return visible and is_visible_type(parent_type, source)


def is_required(node: Node, source: bytes, path: str) -> bool:
    if node.type == "package_declaration":
        return PurePosixPath(path).name == "package-info.java"
    if node.type == "module_declaration":
        return PurePosixPath(path).name == "module-info.java"
    if node.type in TYPE_NODES:
        return is_visible_type(node, source)
    parent_type = nearest_type(node)
    if parent_type is None or not is_visible_type(parent_type, source):
        return False
    if node.type == "enum_constant":
        return True
    parent = node.parent
    modifiers = modifiers_text(node, source)
    if parent is not None and parent.type in ("annotation_type_body", "interface_body"):
        return not bool(re.search(r"\bprivate\b", modifiers))
    return bool(re.search(r"\b(public|protected)\b", modifiers))


def documentable_nodes(root: Node, path: str) -> Iterable[Node]:
    for node in walk(root):
        if node.type in TYPE_NODES or node.type in MEMBER_NODES:
            yield node
        elif node.type == "enum_constant" and node.parent is not None:
            if node.parent.type == "enum_body":
                yield node
        elif (
            node.type == "package_declaration"
            and PurePosixPath(path).name == "package-info.java"
        ) or (
            node.type == "module_declaration"
            and PurePosixPath(path).name == "module-info.java"
        ):
            yield node


def declaration_key(node: Node, source: bytes) -> str:
    parents = ".".join(parent_type_path(node, source))
    name = declaration_name(node, source)
    parameters = node_text(node.child_by_field_name("parameters"), source)
    return "|".join((parents, node.type, name, re.sub(r"\s+", " ", parameters)))


def scan_declarations(source: bytes, *, path: str) -> list[Declaration]:
    try:
        source.decode("utf-8")
    except UnicodeDecodeError as error:
        raise JavadocError(f"Java 檔案不是 UTF-8：{path}") from error
    root = parse_java(source, path=path)
    declarations: list[Declaration] = []
    for node in documentable_nodes(root, path):
        javadoc = find_javadoc(node, source)
        metadata = {
            "parameters": parameter_names(node, source),
            "type_parameters": type_parameter_names(node, source),
            "record_components": record_component_names(node, source),
            "return_type": return_type(node, source),
            "throws": thrown_types(node, source),
            "deprecated": "@Deprecated" in modifiers_text(node, source),
        }
        declarations.append(
            Declaration(
                key=declaration_key(node, source),
                kind=node.type,
                name=declaration_name(node, source),
                line=node.start_point.row + 1,
                start_byte=node.start_byte,
                end_byte=node.end_byte,
                javadoc_start=javadoc[0] if javadoc else None,
                javadoc_end=javadoc[1] if javadoc else None,
                required=is_required(node, source, path),
                metadata=metadata,
            )
        )
    keys = [item.key for item in declarations]
    if len(keys) != len(set(keys)):
        duplicates = sorted({key for key in keys if keys.count(key) > 1})
        raise JavadocError(f"無法唯一識別 Java 宣告：{duplicates[:3]}")
    return declarations


def target_declarations(source: bytes, *, path: str) -> list[Declaration]:
    return [
        item
        for item in scan_declarations(source, path=path)
        if item.required or item.has_javadoc
    ]


def without_attached_javadocs(source: bytes, *, path: str) -> bytes:
    """移除宣告前相鄰的 Javadoc，用來證明其餘 Java 位元完全未變。"""
    ranges = {
        (item.javadoc_start, item.start_byte)
        for item in scan_declarations(source, path=path)
        if item.has_javadoc
    }
    result = source
    for start, end in sorted(ranges, reverse=True):
        assert start is not None and end is not None
        result = result[:start] + result[end:]
    return result


def normalize_relative_path(value: str) -> str:
    raw = value.strip().removeprefix("@")
    if "\\" in raw:
        raise JavadocError("只能使用專案相對的正斜線路徑")
    relative = PurePosixPath(raw)
    if relative.is_absolute() or any(
        part in ("", ".", "..") for part in relative.parts
    ):
        raise JavadocError("必須使用專案相對路徑")
    return relative.as_posix()


def maven_modules(root: Path) -> list[Path]:
    seen: set[Path] = set()
    modules: list[Path] = []

    def visit(directory: Path) -> None:
        resolved = directory.resolve()
        try:
            resolved.relative_to(root.resolve())
        except ValueError as error:
            raise JavadocError(f"Maven 模組離開專案目錄：{directory}") from error
        if resolved in seen:
            return
        seen.add(resolved)
        pom = resolved / "pom.xml"
        if not pom.is_file():
            raise JavadocError(f"Maven 模組缺少 pom.xml：{pom}")
        modules.append(resolved)
        try:
            tree = ET.parse(pom)
        except ET.ParseError as error:
            raise JavadocError(f"pom.xml 無法解析：{pom}") from error
        root_node = tree.getroot()
        for modules_node in root_node:
            if modules_node.tag.rsplit("}", 1)[-1] != "modules":
                continue
            for node in modules_node:
                if (
                    node.tag.rsplit("}", 1)[-1] == "module"
                    and node.text
                    and node.text.strip()
                ):
                    visit(resolved / node.text.strip())

    visit(root)
    return modules


def maven_source_roots(root: Path) -> list[Path]:
    return [
        module / "src/main/java"
        for module in maven_modules(root)
        if (module / "src/main/java").is_dir()
    ]


def java_files(root: Path, target_path: str | None = None) -> list[str]:
    source_roots = maven_source_roots(root)
    if target_path:
        relative = normalize_relative_path(target_path)
        target = root.joinpath(*PurePosixPath(relative).parts)
        if not target.is_file() or target.suffix != ".java":
            raise JavadocError(f"指定目標不是既有 Java 檔案：{relative}")
        if target.is_symlink():
            raise JavadocError("Java 檔案不得是符號連結")
        resolved_target = target.resolve()
        if not any(
            resolved_target.is_relative_to(source_root.resolve())
            for source_root in source_roots
        ):
            raise JavadocError("指定 Java 檔案不在 Maven src/main/java 內")
        return [relative]
    files = []
    for source_root in source_roots:
        files.extend(
            path.relative_to(root).as_posix()
            for path in source_root.rglob("*.java")
            if path.is_file() and not path.is_symlink()
        )
    return sorted(set(files))


def git_common_dir(repo: Path) -> Path:
    value = git(repo, "rev-parse", "--git-common-dir")
    path = Path(value)
    if not path.is_absolute():
        path = repo / path
    return path.resolve()


def state_path(repo: Path, run_id: str) -> Path:
    return git_common_dir(repo) / "opencode-javadoc" / f"{run_id}.json"


def save_state(repo: Path, state: dict[str, Any]) -> None:
    destination = state_path(repo, state["run_id"])
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=destination.parent, prefix=f".{destination.name}."
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(state, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def load_state(repo: Path, worktree: str) -> dict[str, Any]:
    relative = normalize_relative_path(worktree)
    parts = PurePosixPath(relative).parts
    if len(parts) != 2 or parts[0] != "javadoc-worktrees":
        raise JavadocError("worktree 格式不正確")
    run_id = parts[1]
    path = state_path(repo, run_id)
    if not path.is_file():
        raise JavadocError(f"找不到 Javadoc 執行狀態：{run_id}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("worktree") != relative:
        raise JavadocError("Javadoc 執行狀態與 worktree 不一致")
    return value


@contextmanager
def locked_state(repo: Path, worktree: str) -> Iterable[dict[str, Any]]:
    """鎖住同一執行的狀態，避免平行逐檔 Agent 互相覆蓋。"""
    initial = load_state(repo, worktree)
    lock_path = state_path(repo, initial["run_id"]).with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            current = load_state(repo, worktree)
            yield current
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def resolve_worktree(repo: Path, relative: str) -> Path:
    value = load_state(repo, relative)
    worktree = repo.joinpath(*PurePosixPath(value["worktree"]).parts).resolve()
    expected_parent = (repo / "javadoc-worktrees").resolve()
    if worktree.parent != expected_parent or not worktree.is_dir():
        raise JavadocError("Javadoc worktree 不存在或路徑不合法")
    return worktree


def declaration_from_state(value: dict[str, Any]) -> Declaration:
    return Declaration(
        key=value["key"],
        kind=value["kind"],
        name=value["name"],
        line=value["line"],
        start_byte=value["start_byte"],
        end_byte=value["end_byte"],
        javadoc_start=value.get("javadoc_start"),
        javadoc_end=value.get("javadoc_end"),
        required=value["required"],
        metadata=value.get("metadata", {}),
    )


def declaration_to_state(value: Declaration) -> dict[str, Any]:
    return asdict(value)


def normalize_javadoc(value: str) -> str:
    normalized = value.replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    if not normalized.strip():
        raise JavadocError("Javadoc 內文不得為空")
    if normalized.lstrip().startswith("/**") or normalized.rstrip().endswith("*/"):
        raise JavadocError("Javadoc 只能提供內文，不得包含 /** 或 */")
    if "\x00" in normalized or "*/" in normalized or re.search(r"\\u+", normalized):
        raise JavadocError("Javadoc 內文包含不安全字元")
    normalized.encode("utf-8")
    return normalized


def validate_javadoc_body(body: str, declaration: Declaration) -> None:
    if "{@inheritDoc}" in body:
        return
    parameter_names_required = [
        *declaration.metadata.get("record_components", []),
        *declaration.metadata.get("parameters", []),
    ]
    for name in dict.fromkeys(parameter_names_required):
        if not re.search(rf"(?m)^\s*@param\s+{re.escape(name)}\b", body):
            raise JavadocError(f"{declaration.name} 缺少 @param {name}")
    for name in declaration.metadata.get("type_parameters", []):
        if not re.search(rf"(?m)^\s*@param\s+<{re.escape(name)}>(?:\s|$)", body):
            raise JavadocError(f"{declaration.name} 缺少 @param <{name}>")
    return_value = declaration.metadata.get("return_type", "")
    if (
        return_value
        and return_value != "void"
        and "@return" not in body
        and "{@return" not in body
    ):
        raise JavadocError(f"{declaration.name} 缺少 @return")
    for thrown in declaration.metadata.get("throws", []):
        simple = thrown.rsplit(".", 1)[-1]
        if not re.search(
            rf"(?m)^\s*@throws\s+(?:[\w.]*\.)?{re.escape(simple)}\b", body
        ):
            raise JavadocError(f"{declaration.name} 缺少 @throws {thrown}")
    if declaration.metadata.get("deprecated") and "@deprecated" not in body:
        raise JavadocError(f"{declaration.name} 缺少 @deprecated")


def render_javadoc(indentation: bytes, body: str, newline: bytes) -> bytes:
    result = bytearray(b"/**" + newline)
    for line in body.split("\n"):
        result.extend(indentation + b" *")
        if line:
            result.extend(b" " + line.encode("utf-8"))
        result.extend(newline)
    result.extend(indentation + b" */")
    return bytes(result)
