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


class ValidationError(RuntimeError):
    pass


@dataclass(frozen=True)
class Declaration:
    kind: str
    name: str
    line: int
    start: int
    javadoc_start: int | None
    javadoc_end: int | None
    required: bool

    @property
    def has_javadoc(self) -> bool:
        return self.javadoc_start is not None


def command(
    arguments: list[str],
    *,
    cwd: Path,
    message: str,
    timeout: int = 120,
    environment: dict[str, str] | None = None,
) -> str:
    try:
        result = subprocess.run(
            arguments,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            env=environment,
        )
    except FileNotFoundError as error:
        raise ValidationError(f"找不到必要指令：{arguments[0]}") from error
    except subprocess.TimeoutExpired as error:
        raise ValidationError(f"指令逾時：{' '.join(arguments)}") from error
    if result.returncode != 0:
        detail = (result.stdout + result.stderr).strip()[-4_000:]
        raise ValidationError(message + (f"：{detail}" if detail else ""))
    return result.stdout.strip()


def git(repo: Path, *arguments: str, message: str = "Git 指令失敗") -> str:
    return command(["git", "-C", str(repo), *arguments], cwd=repo, message=message)


def maven_environment() -> dict[str, str]:
    environment = os.environ.copy()
    java_home = environment.get("JAVA_HOME")
    if not java_home:
        raise ValidationError("JAVA_HOME 未設定，無法執行 Maven 編譯")
    home = Path(java_home)
    if not (os.access(home / "bin/java", os.X_OK) and os.access(home / "bin/javac", os.X_OK)):
        raise ValidationError(f"JAVA_HOME 指向的 JDK 無效：{java_home}")
    return environment


def normalize_path(value: str) -> str:
    raw = value.strip().removeprefix("@")
    relative = PurePosixPath(raw)
    if (
        "\\" in raw
        or relative.is_absolute()
        or any(part in ("", ".", "..") for part in relative.parts)
    ):
        raise ValidationError("必須使用專案相對的正斜線路徑")
    return relative.as_posix()


def state_file(repo: Path, worktree: str) -> Path:
    parts = PurePosixPath(normalize_path(worktree)).parts
    if len(parts) != 2 or parts[0] != "javadoc-worktrees":
        raise ValidationError("worktree 格式不正確")
    common = Path(git(repo, "rev-parse", "--git-common-dir"))
    if not common.is_absolute():
        common = repo / common
    return common.resolve() / "opencode-javadoc" / f"{parts[1]}.json"


def load_state(repo: Path, worktree: str) -> dict[str, Any]:
    path = state_file(repo, worktree)
    if not path.is_file():
        raise ValidationError("找不到 Javadoc 執行狀態")
    state = json.loads(path.read_text(encoding="utf-8"))
    if state.get("worktree") != normalize_path(worktree):
        raise ValidationError("執行狀態與 worktree 不一致")
    return state


def save_state(repo: Path, state: dict[str, Any]) -> None:
    destination = state_file(repo, state["worktree"])
    descriptor, temporary_name = tempfile.mkstemp(dir=destination.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(state, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def resolve_worktree(repo: Path, relative: str) -> Path:
    worktree = repo.joinpath(*PurePosixPath(relative).parts).resolve()
    if worktree.parent != (repo / "javadoc-worktrees").resolve() or not worktree.is_dir():
        raise ValidationError("Javadoc worktree 不存在或路徑不合法")
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
        raise ValidationError(f"無法讀取基準檔案：{path}")
    return result.stdout


def walk(node: Node) -> Iterable[Node]:
    yield node
    for child in node.named_children:
        yield from walk(child)


def text(node: Node | None, source: bytes) -> str:
    return "" if node is None else source[node.start_byte : node.end_byte].decode()


def modifiers(node: Node, source: bytes) -> str:
    return next(
        (text(child, source) for child in node.named_children if child.type == "modifiers"),
        "",
    )


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


def visible_type(node: Node, source: bytes) -> bool:
    parent_type = nearest_type(node)
    value = modifiers(node, source)
    if parent_type is None:
        return bool(re.search(r"\bpublic\b", value))
    parent = node.parent
    visible = (
        parent is not None
        and parent.type in ("annotation_type_body", "interface_body")
    ) or bool(re.search(r"\b(public|protected)\b", value))
    return visible and visible_type(parent_type, source)


def required(node: Node, source: bytes, filename: str) -> bool:
    if node.type == "package_declaration":
        return filename == "package-info.java"
    if node.type == "module_declaration":
        return filename == "module-info.java"
    if node.type in TYPE_NODES:
        return visible_type(node, source)
    parent_type = nearest_type(node)
    if parent_type is None or not visible_type(parent_type, source):
        return False
    if node.type == "enum_constant":
        return True
    parent = node.parent
    value = modifiers(node, source)
    if parent is not None and parent.type in ("annotation_type_body", "interface_body"):
        return not bool(re.search(r"\bprivate\b", value))
    return bool(re.search(r"\b(public|protected)\b", value))


def scan(source: bytes, path: str) -> list[Declaration]:
    try:
        source.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValidationError(f"Java 檔案不是 UTF-8：{path}") from error
    root = Parser(Language(tree_sitter_java.language())).parse(source).root_node
    if root.has_error:
        raise ValidationError(f"Java 原始碼無法完整解析：{path}")
    filename = PurePosixPath(path).name
    declarations = []
    for node in walk(root):
        documentable = node.type in TYPE_NODES or node.type in MEMBER_NODES
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
        javadoc_start = None
        javadoc_end = None
        if previous is not None and previous.type == "block_comment":
            raw = source[previous.start_byte : previous.end_byte]
            gap = source[previous.end_byte : node.start_byte]
            if raw.startswith(b"/**") and not gap.strip():
                javadoc_start = previous.start_byte
                javadoc_end = previous.end_byte
        declarations.append(
            Declaration(
                kind=node.type,
                name=declaration_name(node, source),
                line=declaration_line(node),
                start=node.start_byte,
                javadoc_start=javadoc_start,
                javadoc_end=javadoc_end,
                required=required(node, source, filename),
            )
        )
    return declarations


def strip_javadocs(source: bytes, path: str) -> bytes:
    result = source
    ranges = {
        (item.javadoc_start, item.start)
        for item in scan(source, path)
        if item.has_javadoc
    }
    for start, end in sorted(ranges, reverse=True):
        assert start is not None
        result = result[:start] + result[end:]
    return result


def validate(repo: Path, payload: dict[str, Any]) -> dict[str, Any]:
    worktree_name, results = payload.get("worktree"), payload.get("file_results")
    if not isinstance(worktree_name, str) or not isinstance(results, list):
        raise ValidationError("worktree 必須是字串，file_results 必須是陣列")
    state = load_state(repo, worktree_name)
    if state.get("status") != "prepared":
        raise ValidationError("目前執行狀態不可驗證")
    worktree = resolve_worktree(repo, worktree_name)
    expected = set(state["files"])
    outcomes = {}
    for item in results:
        if not isinstance(item, dict):
            raise ValidationError("每個 file_result 都必須是物件")
        path, status = item.get("path"), item.get("status")
        if not isinstance(path, str) or path not in expected or path in outcomes:
            raise ValidationError("file_results 含未知或重複路徑")
        if status not in ("completed", "failed"):
            raise ValidationError("檔案狀態只能是 completed 或 failed")
        outcomes[path] = {
            "status": status,
            "message": str(item.get("message", "")),
        }
    if set(outcomes) != expected:
        raise ValidationError(f"尚未回報所有檔案：{sorted(expected - set(outcomes))[:10]}")

    failed, completed = [], set()

    def reject_file(path: str, reason: str) -> None:
        git(
            worktree,
            "restore",
            "--source",
            state["base_sha"],
            "--",
            path,
            message=f"無法還原未完成檔案：{path}",
        )
        failed.append({"path": path, "reason": reason})

    for path, outcome in outcomes.items():
        if outcome["status"] == "failed":
            reject_file(path, outcome["message"] or "逐檔子代理未完成")
            continue
        try:
            source = (worktree / path).read_bytes()
            declarations = scan(source, path)
            missing = [
                item
                for item in declarations
                if item.required
                and not item.has_javadoc
            ]
            if missing:
                raise ValidationError(
                    f"{path} 仍有必要宣告缺少 Javadoc："
                    f"{[f'{item.name}@{item.line}' for item in missing[:10]]}"
                )
            if strip_javadocs(
                git_blob(worktree, state["base_sha"], path), path
            ) != strip_javadocs(source, path):
                raise ValidationError(f"{path} 含 Javadoc 以外的變更")
        except (ValidationError, OSError) as error:
            reject_file(path, f"檔案驗證失敗：{error}")
            continue
        completed.add(path)

    changed = [
        line
        for line in git(
            worktree, "diff", "--name-only", state["base_sha"], "--"
        ).splitlines()
        if line
    ]
    unexpected = [path for path in changed if path not in completed]
    if unexpected:
        raise ValidationError(f"發現範圍外變更：{unexpected[:10]}")
    git(
        worktree,
        "diff",
        "--check",
        state["base_sha"],
        "--",
        *changed,
        message="Git 差異格式檢查失敗",
    )

    maven = ["./mvnw"] if (worktree / "mvnw").is_file() and os.access(worktree / "mvnw", os.X_OK) else ["mvn"]
    arguments = [*maven, "-B", "-ntp", "-DskipTests", "compile"]
    environment = maven_environment()
    output = command(
        arguments,
        cwd=worktree,
        timeout=900,
        message="Maven 編譯失敗",
        environment=environment,
    )
    commands = [{"name": "compile", "command": " ".join(arguments), "output": output[-2_000:]}]
    if any(
        "maven-javadoc-plugin" in pom.read_text(encoding="utf-8")
        for pom in worktree.rglob("pom.xml")
        if "target" not in pom.parts
    ):
        arguments = [*maven, "-B", "-ntp", "-DskipTests", "javadoc:javadoc"]
        output = command(
            arguments,
            cwd=worktree,
            timeout=900,
            message="Maven Javadoc 檢查失敗",
            environment=environment,
        )
        commands.append(
            {"name": "javadoc", "command": " ".join(arguments), "output": output[-2_000:]}
        )

    validation = {
        "changed_files": changed,
        "failed_files": failed,
        "commands": commands,
    }
    state["status"] = "validated"
    state["validation"] = validation
    save_state(repo, state)
    return {"status": "validated", **validation}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True, type=Path)
    arguments = parser.parse_args()
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise ValidationError("輸入必須是 JSON 物件")
        result = validate(arguments.repo.resolve(), payload)
    except (ValidationError, json.JSONDecodeError, OSError) as error:
        result = {"status": "rejected", "message": str(error)}
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
