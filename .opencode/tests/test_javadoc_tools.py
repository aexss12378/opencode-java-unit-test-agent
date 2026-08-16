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

from apply_javadocs import ApplyError, apply, scan as apply_scan, without_javadocs
from prepare_javadoc_workspace import java_files, prepare
from publish_javadocs import GitHubPublisher, publish
from validate_javadocs import scan as validation_scan
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
        remote, repo = root / "remote.git", root / "repo"
        command("git", "init", "--bare", "--initial-branch=main", str(remote), cwd=root)
        command("git", "init", "--initial-branch=main", str(repo), cwd=root)
        command("git", "config", "user.name", "Javadoc Test", cwd=repo)
        command("git", "config", "user.email", "javadoc@example.invalid", cwd=repo)
        (repo / "src/main/java/example").mkdir(parents=True)
        (repo / "module-a/src/main/java/example").mkdir(parents=True)
        (repo / "pom.xml").write_text("<project/>\n", encoding="utf-8")
        (repo / "module-a/pom.xml").write_text("<project/>\n", encoding="utf-8")
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
        (repo / "module-a/src/main/java/example/Module.java").write_text(
            "package example;\npublic class Module {}\n", encoding="utf-8"
        )
        command("git", "add", ".", cwd=repo)
        command("git", "commit", "-m", "fixture", cwd=repo)
        command("git", "remote", "add", "origin", str(remote), cwd=repo)
        command("git", "push", "-u", "origin", "main", cwd=repo)
        return temporary, repo

    def test_scanner_finds_required_public_api(self) -> None:
        source = (
            b"package example;\n"
            b"public record Box<T>(T value) {\n"
            b"  protected T get() { return value; }\n"
            b"  private void hidden() {}\n"
            b"}\n"
        )
        declarations = validation_scan(source, "src/main/java/example/Box.java")
        self.assertTrue(next(item for item in declarations if item.name == "Box").required)
        self.assertTrue(next(item for item in declarations if item.name == "get").required)
        self.assertFalse(next(item for item in declarations if item.name == "hidden").required)

    def test_git_lists_root_and_module_standard_java_paths(self) -> None:
        temporary, repo = self.make_repo()
        self.addCleanup(temporary.cleanup)
        self.assertEqual(
            java_files(repo),
            [
                "module-a/src/main/java/example/Module.java",
                "src/main/java/example/Sample.java",
            ],
        )

    def test_apply_validate_and_publish_javadoc_only_diff(self) -> None:
        temporary, repo = self.make_repo()
        self.addCleanup(temporary.cleanup)
        prepared = prepare(repo, {"target_path": "src/main/java/example/Sample.java"})
        worktree_name = prepared["worktree"]
        path = prepared["files"][0]["path"]
        worktree = repo / worktree_name
        before = (worktree / path).read_bytes()
        by_name = {
            item.name: item for item in apply_scan(before, path)
        }

        with self.assertRaises(ApplyError):
            apply(
                repo,
                {
                    "worktree": worktree_name,
                    "path": path,
                    "changes": [
                        {
                            "line": by_name["Sample"].line,
                            "name": "Sample",
                            "javadoc": "A sample.",
                        },
                        {"line": 999, "name": "missing", "javadoc": "Missing."},
                    ],
                },
            )
        self.assertEqual((worktree / path).read_bytes(), before)

        result = apply(
            repo,
            {
                "worktree": worktree_name,
                "path": path,
                "changes": [
                    {
                        "line": by_name["Sample"].line,
                        "name": "Sample",
                        "javadoc": "A sample type.",
                    },
                    {
                        "line": by_name["greet"].line,
                        "name": "greet",
                        "javadoc": "Returns the supplied name.\n\n@param name supplied name\n@return supplied name",
                    },
                ],
            },
        )
        self.assertTrue(result["changed"])
        after = (worktree / path).read_bytes()
        self.assertEqual(
            without_javadocs(before, path),
            without_javadocs(after, path),
        )
        validation = validate(
            repo,
            {
                "worktree": worktree_name,
                "file_results": [{"path": path, "status": "completed"}],
            },
        )
        self.assertEqual(validation["changed_files"], [path])
        with (
            patch.object(
                GitHubPublisher,
                "create_draft",
                return_value="https://github.example/pull/1",
            ),
            patch.object(
                GitHubPublisher,
                "verify",
                return_value={"isDraft": True},
            ),
        ):
            publication = publish(
                repo, {"worktree": worktree_name, "publisher": "github"}
            )
        self.assertEqual(publication["status"], "published")
        self.assertFalse(worktree.exists())

    def test_failed_file_is_restored_before_validation(self) -> None:
        temporary, repo = self.make_repo()
        self.addCleanup(temporary.cleanup)
        prepared = prepare(repo, {"target_path": "src/main/java/example/Sample.java"})
        worktree_name = prepared["worktree"]
        path = prepared["files"][0]["path"]
        declaration = next(
            item
            for item in apply_scan((repo / worktree_name / path).read_bytes(), path)
            if item.name == "Sample"
        )
        apply(
            repo,
            {
                "worktree": worktree_name,
                "path": path,
                "changes": [
                    {
                        "line": declaration.line,
                        "name": declaration.name,
                        "javadoc": "A sample.",
                    }
                ],
            },
        )
        validation = validate(
            repo,
            {
                "worktree": worktree_name,
                "file_results": [
                    {"path": path, "status": "failed", "message": "fixture failure"}
                ],
            },
        )
        self.assertEqual(validation["changed_files"], [])
        self.assertEqual(publish(repo, {"worktree": worktree_name})["status"], "no-changes")

    def test_document_conflict_can_explain_missing_javadoc(self) -> None:
        temporary, repo = self.make_repo()
        self.addCleanup(temporary.cleanup)
        prepared = prepare(repo, {"target_path": "src/main/java/example/Sample.java"})
        worktree_name = prepared["worktree"]
        path = prepared["files"][0]["path"]
        declarations = [
            item
            for item in validation_scan((repo / worktree_name / path).read_bytes(), path)
            if item.required
        ]
        blocked = [
            {"line": item.line, "name": item.name, "reason": "fixture conflict"}
            for item in declarations
        ]
        validation = validate(
            repo,
            {
                "worktree": worktree_name,
                "file_results": [
                    {
                        "path": path,
                        "status": "completed",
                        "blocked_declarations": blocked,
                    }
                ],
            },
        )
        self.assertEqual(len(validation["blocked_declarations"]), len(declarations))


if __name__ == "__main__":
    unittest.main()
