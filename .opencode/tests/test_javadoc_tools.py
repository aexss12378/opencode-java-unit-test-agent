# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "tree-sitter==0.26.0",
#   "tree-sitter-java==0.23.5",
# ]
# ///

from __future__ import annotations

import os
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
from validate_javadocs import (
    ValidationError,
    maven_environment,
    scan as validation_scan,
    validate,
)


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

    def test_scanners_report_the_declaration_name_line_after_annotations(self) -> None:
        source = (
            b"@Deprecated\n"
            b"public class Sample {\n"
            b"  @Deprecated\n"
            b"  public void run() {}\n"
            b"}\n"
        )
        for scanner in (apply_scan, validation_scan):
            with self.subTest(scanner=scanner.__module__):
                by_name = {
                    item.name: item
                    for item in scanner(source, "src/main/java/example/Sample.java")
                }
                self.assertEqual(by_name["Sample"].line, 2)
                self.assertEqual(by_name["run"].line, 4)

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

    def test_apply_rejects_comment_markers_and_leading_stars_atomically(self) -> None:
        temporary, repo = self.make_repo()
        self.addCleanup(temporary.cleanup)
        prepared = prepare(repo, {"target_path": "src/main/java/example/Sample.java"})
        worktree_name = prepared["worktree"]
        path = prepared["files"][0]["path"]
        source_path = repo / worktree_name / path
        before = source_path.read_bytes()
        declaration = next(
            item for item in apply_scan(before, path) if item.name == "Sample"
        )
        for invalid in ("/** wrapped */", "Summary.\n * stray marker"):
            with self.subTest(invalid=invalid), self.assertRaises(ApplyError):
                apply(
                    repo,
                    {
                        "worktree": worktree_name,
                        "path": path,
                        "changes": [
                            {
                                "line": declaration.line,
                                "name": declaration.name,
                                "javadoc": invalid,
                            }
                        ],
                    },
                )
            self.assertEqual(source_path.read_bytes(), before)

    def test_maven_environment_derives_java_home_from_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary) / "jdk"
            (home / "bin").mkdir(parents=True)
            for name in ("java", "javac"):
                executable = home / "bin" / name
                executable.write_text("#!/bin/sh\n", encoding="utf-8")
                executable.chmod(0o755)
            with (
                patch.dict(os.environ, {"PATH": str(home / "bin")}, clear=True),
                patch(
                    "validate_javadocs.shutil.which",
                    side_effect=lambda name: str(home / "bin" / name),
                ),
            ):
                self.assertEqual(
                    Path(maven_environment()["JAVA_HOME"]).resolve(), home.resolve()
                )

    def test_writer_delegates_control_flow_to_orchestrator(self) -> None:
        writer = (TOOLS.parent / "agents/javadoc-writer.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("呼叫一次 `run_javadocs`", writer)
        self.assertNotIn("run_in_background", writer)
        self.assertNotIn("background_output", writer)

    def test_orchestrator_runs_workers_in_prepared_worktree(self) -> None:
        plugin = (TOOLS.parent / "plugins/javadoc-orchestrator.ts").read_text(
            encoding="utf-8"
        )
        self.assertIn("const workerDirectory = path.resolve(root, worktree)", plugin)
        self.assertIn("query: { directory: workerDirectory }", plugin)
        self.assertNotIn("query: { directory: root }", plugin)

    def test_worker_uses_project_javadoc_skill(self) -> None:
        worker = (TOOLS.parent / "agents/javadoc-worker.md").read_text(
            encoding="utf-8"
        )
        skill = (TOOLS.parent / "skills/javadoc/SKILL.md").read_text(
            encoding="utf-8"
        )
        self.assertIn('skill:\n    "*": deny\n    javadoc: allow', worker)
        self.assertIn("先載入 `javadoc` Skill", worker)
        self.assertIn("reasoning:\n  enabled: false", worker)
        self.assertTrue(skill.startswith("---\nname: javadoc\n"))
        self.assertIn("不得將單一實作、工廠方法或呼叫方式推廣", skill)

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
            worktree,
            {
                "worktree": worktree_name,
                "path": path,
                "changes": [
                    {
                        "line": by_name["Sample"].line,
                        "name": "Sample",
                        "javadoc": "A sample type.\n\n@param <T> value type",
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
