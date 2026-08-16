# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "tree-sitter==0.26.0",
#   "tree-sitter-java==0.23.5",
# ]
# ///

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

from apply_javadocs import apply
from javadoc_common import (
    JavadocError,
    maven_source_roots,
    scan_declarations,
    without_attached_javadocs,
)
from prepare_javadoc_workspace import prepare
from publish_javadocs import GitHubPublisher, publish
from validate_javadocs import validate


def command(*arguments: str, cwd: Path) -> str:
    result = subprocess.run(
        arguments, cwd=cwd, text=True, capture_output=True, check=False
    )
    if result.returncode != 0:
        raise AssertionError(result.stdout + result.stderr)
    return result.stdout.strip()


class JavadocToolsTest(unittest.TestCase):
    def make_repo(self) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        remote = root / "remote.git"
        repo = root / "repo"
        command("git", "init", "--bare", "--initial-branch=main", str(remote), cwd=root)
        command("git", "init", "--initial-branch=main", str(repo), cwd=root)
        command("git", "config", "user.name", "Javadoc Test", cwd=repo)
        command("git", "config", "user.email", "javadoc@example.invalid", cwd=repo)
        (repo / "src/main/java/example").mkdir(parents=True)
        (repo / "pom.xml").write_text(
            '<project xmlns="http://maven.apache.org/POM/4.0.0">'
            "<modelVersion>4.0.0</modelVersion><groupId>x</groupId>"
            "<artifactId>x</artifactId><version>1</version></project>\n",
            encoding="utf-8",
        )
        (repo / ".gitignore").write_text(
            "target/\njavadoc-worktrees/\n", encoding="utf-8"
        )
        wrapper = repo / "mvnw"
        wrapper.write_text("#!/bin/sh\necho fixture-maven-ok\n", encoding="utf-8")
        wrapper.chmod(0o755)
        (repo / "src/main/java/example/Sample.java").write_text(
            "package example;\n\n"
            "public class Sample<T> {\n"
            "    public String greet(String name) throws IllegalArgumentException {\n"
            "        return name;\n"
            "    }\n"
            "}\n",
            encoding="utf-8",
        )
        command("git", "add", ".", cwd=repo)
        command("git", "commit", "-m", "fixture", cwd=repo)
        command("git", "remote", "add", "origin", str(remote), cwd=repo)
        command("git", "push", "-u", "origin", "main", cwd=repo)
        command("git", "remote", "set-head", "origin", "-a", cwd=repo)
        return temporary, repo

    def test_scanner_finds_public_api_and_type_parameters(self) -> None:
        source = (
            b"package example;\n"
            b"public record Box<T>(T value) {\n"
            b"  protected T get() { return value; }\n"
            b"  private void hidden() {}\n"
            b"}\n"
        )
        declarations = scan_declarations(source, path="src/main/java/example/Box.java")
        record = next(
            item for item in declarations if item.kind == "record_declaration"
        )
        self.assertTrue(record.required)
        self.assertEqual(record.metadata["type_parameters"], ["T"])
        self.assertEqual(record.metadata["record_components"], ["value"])
        hidden = next(item for item in declarations if item.name == "hidden")
        self.assertFalse(hidden.required)

    def test_maven_modules_support_poms_without_xml_namespace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "module-a/src/main/java").mkdir(parents=True)
            (root / "pom.xml").write_text(
                "<project><modules><module>module-a</module></modules></project>",
                encoding="utf-8",
            )
            (root / "module-a/pom.xml").write_text("<project/>", encoding="utf-8")
            self.assertEqual(
                maven_source_roots(root), [(root / "module-a/src/main/java").resolve()]
            )

    def test_apply_validate_and_publish_javadoc_only_diff(self) -> None:
        temporary, repo = self.make_repo()
        self.addCleanup(temporary.cleanup)
        prepared = prepare(repo, {"target_path": "src/main/java/example/Sample.java"})
        worktree_name = prepared["worktree"]
        path = prepared["files"][0]["path"]
        targets = prepared["files"][0]["targets"]
        worktree = repo / worktree_name
        before = (worktree / path).read_bytes()
        by_name = {item["name"]: item for item in targets}

        with self.assertRaises(JavadocError):
            apply(
                repo,
                {
                    "worktree": worktree_name,
                    "path": path,
                    "reviews": [
                        {
                            "key": by_name["Sample"]["key"],
                            "decision": "write",
                            "javadoc": "A sample.\n\n@param <T> value type",
                        },
                        {
                            "key": by_name["greet"]["key"],
                            "decision": "write",
                            "javadoc": "Returns a greeting.",
                        },
                    ],
                },
            )
        self.assertEqual((worktree / path).read_bytes(), before)

        result = apply(
            repo,
            {
                "worktree": worktree_name,
                "path": path,
                "reviews": [
                    {
                        "key": by_name["Sample"]["key"],
                        "decision": "write",
                        "javadoc": "A sample.\n\n@param <T> value type",
                    },
                    {
                        "key": by_name["greet"]["key"],
                        "decision": "write",
                        "javadoc": "Returns the supplied name.\n\n@param name supplied name\n@return supplied name\n@throws IllegalArgumentException when rejected",
                    },
                ],
            },
        )
        self.assertEqual(result["remaining"], [])
        after = (worktree / path).read_bytes()
        self.assertEqual(
            without_attached_javadocs(before, path=path),
            without_attached_javadocs(after, path=path),
        )
        validation = validate(
            repo,
            {
                "worktree": worktree_name,
                "file_results": [{"path": path, "status": "completed"}],
            },
        )
        self.assertEqual(validation["status"], "validated")
        self.assertEqual(validation["changed_files"], [path])
        self.assertEqual(validation["commands"][0]["name"], "compile")
        with (
            patch.object(
                GitHubPublisher,
                "create_draft",
                return_value="https://github.example/pull/1",
            ),
            patch.object(
                GitHubPublisher,
                "verify",
                return_value={
                    "url": "https://github.example/pull/1",
                    "isDraft": True,
                    "headRefOid": "由發布工具驗證",
                },
            ),
        ):
            publication = publish(
                repo, {"worktree": worktree_name, "publisher": "github"}
            )
        self.assertEqual(publication["status"], "published")
        self.assertTrue(publication["is_draft"])
        self.assertFalse(worktree.exists())
        remote_sha = command(
            "git",
            "ls-remote",
            "origin",
            f"refs/heads/{publication['branch']}",
            cwd=repo,
        ).split()[0]
        self.assertEqual(remote_sha, publication["commit_sha"])

    def test_failed_file_is_restored_before_validation(self) -> None:
        temporary, repo = self.make_repo()
        self.addCleanup(temporary.cleanup)
        prepared = prepare(repo, {"target_path": "src/main/java/example/Sample.java"})
        worktree_name = prepared["worktree"]
        path = prepared["files"][0]["path"]
        target = next(
            item for item in prepared["files"][0]["targets"] if item["name"] == "Sample"
        )
        apply(
            repo,
            {
                "worktree": worktree_name,
                "path": path,
                "reviews": [
                    {
                        "key": target["key"],
                        "decision": "write",
                        "javadoc": "A sample.\n\n@param <T> value type",
                    }
                ],
            },
        )
        result = validate(
            repo,
            {
                "worktree": worktree_name,
                "file_results": [
                    {"path": path, "status": "failed", "message": "fixture failure"}
                ],
            },
        )
        self.assertEqual(result["changed_files"], [])
        self.assertEqual(
            result["failed_files"], [{"path": path, "reason": "fixture failure"}]
        )
        publication = publish(repo, {"worktree": worktree_name, "publisher": "github"})
        self.assertEqual(publication["status"], "no-changes")
        self.assertFalse((repo / worktree_name).exists())


if __name__ == "__main__":
    unittest.main()
