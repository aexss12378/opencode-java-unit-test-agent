from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

BACKEND = Path(__file__).parents[1] / "tools" / "submit_unit_tests.py"


def load_backend() -> ModuleType:
    name = "submit_unit_tests_backend"
    spec = importlib.util.spec_from_file_location(name, BACKEND)
    if spec is None or spec.loader is None:
        raise RuntimeError("無法載入 submit_unit_tests.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


backend = load_backend()


def command(*arguments: str, cwd: Path | None = None, git_dir: Path | None = None) -> subprocess.CompletedProcess[str]:
    invocation = ["git"]
    if git_dir is not None:
        invocation.extend(("--git-dir", str(git_dir)))
    invocation.extend(arguments)
    return subprocess.run(
        invocation,
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def checked(*arguments: str, cwd: Path | None = None, git_dir: Path | None = None) -> str:
    result = command(*arguments, cwd=cwd, git_dir=git_dir)
    if result.returncode != 0:
        raise RuntimeError(result.stdout + result.stderr)
    return result.stdout.strip()


class WorkflowRepositoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="unit-test-workflow-")
        self.root = Path(self.temporary.name)
        self.remote = self.root / "remote.git"
        self.repo = self.root / "repo"
        checked("init", "--bare", str(self.remote))
        checked("init", "-b", "main", str(self.repo))
        checked("config", "user.name", "Unit Test Tool", cwd=self.repo)
        checked("config", "user.email", "unit-test-tool@example.invalid", cwd=self.repo)

        (self.repo / ".gitignore").write_text("target/\n.opencode/node_modules/\n", encoding="utf-8")
        (self.repo / "pom.xml").write_text("<project/>\n", encoding="utf-8")
        wrapper = self.repo / "mvnw"
        wrapper.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        wrapper.chmod(0o755)
        agent = self.repo / ".opencode/agents/unit-test.md"
        agent.parent.mkdir(parents=True)
        agent.write_text("---\nmode: subagent\n---\n", encoding="utf-8")
        source = self.repo / "src/main/java/com/example"
        source.mkdir(parents=True)
        specifications = self.repo / "docs"
        specifications.mkdir()
        for name in ["AlphaService", "BetaService", *[f"Service{index:02d}Service" for index in range(6)]]:
            (source / f"{name}.java").write_text(
                f"package com.example; public class {name} {{}}\n",
                encoding="utf-8",
            )
            (specifications / f"{name}.md").write_text(
                f"# {name} 規格\n",
                encoding="utf-8",
            )
        checked(
            "add",
            "--",
            ".gitignore",
            ".opencode",
            "docs",
            "pom.xml",
            "mvnw",
            "src/main/java",
            cwd=self.repo,
        )
        checked("commit", "-m", "initial", cwd=self.repo)
        checked("remote", "add", "origin", str(self.remote), cwd=self.repo)
        checked("push", "-u", "origin", "main", cwd=self.repo)
        self.base_sha = checked("rev-parse", "HEAD", cwd=self.repo)
        self.base = backend.BaseContext(
            branch="main",
            head_sha=self.base_sha,
            remote="origin",
            remote_branch="main",
            github_host="github.com",
            github_repository="example/repository",
        )
        plugin = self.repo / ".opencode/node_modules/@opencode-ai/plugin"
        plugin.mkdir(parents=True)
        (plugin / "package.json").write_text("{}\n", encoding="utf-8")
        self.worktrees: list[object] = []

    def tearDown(self) -> None:
        backend._CANCEL_REQUESTED = False
        for worktree in reversed(self.worktrees):
            if worktree.project.exists():
                backend.cleanup_worktree(self.repo, worktree, delete_branch=True)
        self.temporary.cleanup()

    @staticmethod
    def target(simple_name: str) -> dict[str, object]:
        target_class = f"com.example.{simple_name}"
        return {
            "target_class": target_class,
            "target_source": f"src/main/java/com/example/{simple_name}.java",
            "candidate_class": f"com.example.{simple_name}Test",
            "test_file": f"src/test/java/com/example/{simple_name}Test.java",
            "specification_sources": [f"docs/{simple_name}.md"],
        }

    @staticmethod
    def cases() -> list[dict[str, str]]:
        return [
            {
                "id": "UT-001",
                "scenario": "建立物件",
                "expected": "建立成功",
                "evidence": "docs/service.md:1",
            }
        ]

    @staticmethod
    def content(simple_name: str) -> str:
        return (
            "package com.example;\n\n"
            f"class {simple_name}Test {{\n"
            "    // UT-001\n"
            "}\n"
        )

    @staticmethod
    def maven_success(_project: Path, candidate_class: str) -> dict[str, object]:
        return {
            "command": f"./mvnw -B -ntp -Dtest={candidate_class} test",
            "exit_code": 0,
            "timed_out": False,
            "maven_errors": "",
        }

    @staticmethod
    def summary_success(_project: Path, candidate_class: str) -> dict[str, object]:
        return {
            "class": candidate_class,
            "tests": 1,
            "executed": 1,
            "skipped": 0,
            "reports": [f"TEST-{candidate_class}.xml"],
            "unexpected_classes": [],
        }

    @staticmethod
    def coverage_success(_project: Path, target_class: str) -> dict[str, object]:
        return {
            "target_class": target_class,
            "counter": "LINE",
            "covered": 4,
            "missed": 0,
            "percent": 100.0,
            "minimum_percent": 80,
            "passed": True,
            "missed_lines": [],
            "xml": "target/site/jacoco/jacoco.xml",
            "exec": "target/jacoco.exec",
        }

    @staticmethod
    def draft_pr(
        _project: Path,
        _request: dict[str, object],
        _validation: dict[str, object],
        assignment: object,
        head_sha: str,
        _digest: str,
    ) -> dict[str, object]:
        return {
            "number": 7,
            "url": f"https://github.com/example/repository/pull/{head_sha[:6]}",
            "isDraft": True,
            "state": "OPEN",
            "headRefName": assignment.branch,
            "headRefOid": head_sha,
            "baseRefName": assignment.base.remote_branch,
        }

    def prepare(self, session_id: str, simple_name: str) -> tuple[object, object]:
        prepared = backend.prepare_assignment(self.repo, self.base, session_id, self.target(simple_name))
        self.worktrees.append(prepared[0])
        return prepared

    def write_candidate(self, worktree: object, assignment: object, simple_name: str) -> dict[str, object]:
        path = worktree.project / assignment.test_file
        path.parent.mkdir(parents=True, exist_ok=True)
        content = self.content(simple_name)
        path.write_text(content, encoding="utf-8")
        return {
            "target_class": assignment.target_class,
            "candidate_class": assignment.candidate_class,
            "test_cases": self.cases(),
            "file": {"path": assignment.test_file, "content": content},
        }

    def submit_success(self, worktree: object, assignment: object, request: dict[str, object]) -> dict[str, object]:
        with (
            patch.object(backend, "verify_assignment_state"),
            patch.object(backend, "run_maven", side_effect=self.maven_success),
            patch.object(backend, "test_summary", side_effect=self.summary_success),
            patch.object(backend, "coverage_summary", side_effect=self.coverage_success),
            patch.object(backend, "create_draft_pr", side_effect=self.draft_pr),
        ):
            return backend.submit(worktree.project, assignment, request)

    def test_prepare_dispatch_creates_one_worktree_per_service(self) -> None:
        request = {
            "execution_mode": "unit-test-all/v1",
            "targets": [self.target("AlphaService"), self.target("BetaService")],
            "not_started": [],
            "target_order": ["com.example.AlphaService", "com.example.BetaService"],
            "max_concurrency": 2,
        }
        with patch.object(backend, "base_context", return_value=self.base):
            result = backend.prepare_dispatch(self.repo, "session-dispatch", request)

        self.assertEqual(result["status"], "prepared")
        self.assertEqual(len(result["prepared"]), 2)
        worktrees = {Path(item["worktree"]) for item in result["prepared"]}
        self.assertEqual(len(worktrees), 2)
        self.assertTrue(all(path.is_dir() for path in worktrees))
        self.assertEqual(len({item["branch"] for item in result["prepared"]}), 2)
        self.assertTrue(all(item["prompt"] for item in result["prepared"]))
        self.assertTrue(
            all("不得呼叫 submit_unit_tests" in item["prompt"] for item in result["prepared"])
        )
        self.assertEqual(checked("worktree", "list", "--porcelain", cwd=self.repo).count("worktree "), 3)
        self.assertEqual(checked("rev-parse", "HEAD", cwd=self.repo), self.base_sha)
        for item in result["prepared"]:
            project = Path(item["worktree"])
            backend.cleanup_worktree(
                self.repo,
                backend.Worktree(project.parent, project, item["branch"]),
                delete_branch=True,
            )

    def test_batch_dispatch_requires_every_concrete_service_to_be_classified(self) -> None:
        not_started = [
            {
                "target_class": f"com.example.Service{index:02d}Service",
                "reason": "缺少可信規格證據",
            }
            for index in range(6)
        ]
        not_started.append(
            {
                "target_class": "com.example.BetaService",
                "reason": "可信規格彼此衝突",
            }
        )
        result = backend.validate_dispatch_request(
            self.repo,
            {
                "execution_mode": "unit-test-all/v1",
                "targets": [
                    {
                        "target_class": "com.example.AlphaService",
                        "specification_sources": ["docs/AlphaService.md"],
                    }
                ],
                "not_started": not_started,
                "max_concurrency": 2,
            },
        )

        self.assertEqual(result["target_order"], backend.discover_concrete_services(self.repo))
        self.assertEqual(len(result["targets"]), 1)
        self.assertEqual(len(result["not_started"]), 7)

    def test_batch_dispatch_accepts_every_service_as_not_started(self) -> None:
        not_started = [
            {
                "target_class": target_class,
                "reason": "缺少可信規格證據",
            }
            for target_class in backend.discover_concrete_services(self.repo)
        ]
        result = backend.validate_dispatch_request(
            self.repo,
            {
                "execution_mode": "unit-test-all/v1",
                "targets": [],
                "not_started": not_started,
                "max_concurrency": 2,
            },
        )

        self.assertEqual(result["targets"], [])
        self.assertEqual(result["target_order"], backend.discover_concrete_services(self.repo))
        with patch.object(backend, "base_context", return_value=self.base):
            prepared = backend.prepare_dispatch(self.repo, "session-no-workers", result)
        self.assertEqual(prepared["status"], "prepared")
        self.assertEqual(prepared["prepared"], [])
        self.assertEqual(len(prepared["results"]), len(not_started))
        self.assertTrue(all(item["status"] == "not-started" for item in prepared["results"]))

    def test_batch_dispatch_rejects_missing_service_classification(self) -> None:
        with self.assertRaisesRegex(backend.RequestError, "未分類"):
            backend.validate_dispatch_request(
                self.repo,
                {
                    "execution_mode": "unit-test-all/v1",
                    "targets": [
                        {
                            "target_class": "com.example.AlphaService",
                            "specification_sources": ["docs/AlphaService.md"],
                        }
                    ],
                    "not_started": [],
                    "max_concurrency": 2,
                },
            )

    def test_dispatch_rejects_source_without_public_javadoc_as_specification(self) -> None:
        with self.assertRaisesRegex(backend.RequestError, "沒有公開 Javadoc"):
            backend.validate_dispatch_request(
                self.repo,
                {
                    "execution_mode": "confirmed-targets",
                    "targets": [
                        {
                            "target_class": "com.example.AlphaService",
                            "specification_sources": [
                                "src/main/java/com/example/AlphaService.java"
                            ],
                        }
                    ],
                    "not_started": [],
                    "max_concurrency": 1,
                },
            )

    def test_link_opencode_dependencies_keeps_scoped_parent_local(self) -> None:
        source_modules = self.root / "source-modules"
        plugin = source_modules / "@opencode-ai" / "plugin"
        plugin.mkdir(parents=True)
        (plugin / "package.json").write_text("{}\n", encoding="utf-8")
        target_modules = self.root / "target-modules"

        backend.link_opencode_dependencies(source_modules, target_modules)

        target_scope = target_modules / "@opencode-ai"
        self.assertTrue(target_scope.is_dir())
        self.assertFalse(target_scope.is_symlink())
        self.assertTrue((target_scope / "plugin").is_symlink())
        self.assertEqual((target_scope / "plugin").resolve(), plugin.resolve())

    def test_assignment_binds_to_exact_child_session(self) -> None:
        worktree, assignment = self.prepare("session-bind", "AlphaService")
        with patch.object(backend, "verify_assignment_state"):
            result = backend.bind_assignment(
                worktree.project,
                "session-bind",
                {
                    "assignment_id": assignment.assignment_id,
                    "worker_session_id": "child-session-alpha",
                },
            )

            self.assertEqual(result["status"], "assignment-bound")
            loaded = backend.load_assignment(worktree.project, "child-session-alpha")
            self.assertEqual(loaded.worker_session_id, "child-session-alpha")
            with self.assertRaisesRegex(backend.RequestError, "子工作階段與派工清單不一致"):
                backend.load_assignment(worktree.project, "different-child")

    def test_finalize_success_cleans_worktree(self) -> None:
        worktree, assignment = self.prepare("session-finalize", "AlphaService")
        with patch.object(backend, "verify_assignment_state"):
            backend.bind_assignment(
                worktree.project,
                "session-finalize",
                {
                    "assignment_id": assignment.assignment_id,
                    "worker_session_id": "child-finalize",
                },
            )
        sha = "c" * 40
        backend.atomic_write_json(
            assignment.result_path,
            {
                "assignment_id": assignment.assignment_id,
                "status": "draft-pr-created",
                "target_class": assignment.target_class,
                "test_file": assignment.test_file,
                "branch": assignment.branch,
                "base_sha": assignment.base.head_sha,
                "pr_created": True,
                "pr_verified": True,
                "pr": {"draft": True, "url": "https://example.invalid/pr"},
                "commit_sha": sha,
                "remote_sha": sha,
            },
            mode=0o600,
        )

        with (
            patch.object(backend, "verify_assignment_state"),
            patch.object(backend, "verify_completed_worker"),
        ):
            result = backend.finalize_assignment(
                worktree.project,
                "session-finalize",
                {
                    "assignment_id": assignment.assignment_id,
                    "worker_session_id": "child-finalize",
                    "worker_message": "完成",
                    "worker_error": "",
                    "cancelled": False,
                },
            )

        self.assertEqual(result["status"], "draft-pr-created")
        self.assertTrue(result["post_worker_verified"])
        self.assertFalse(result["worktree_retained"])
        self.assertFalse(worktree.project.exists())

    def test_finalize_failure_retains_worktree(self) -> None:
        worktree, assignment = self.prepare("session-retain", "AlphaService")
        with patch.object(backend, "verify_assignment_state"):
            backend.bind_assignment(
                worktree.project,
                "session-retain",
                {
                    "assignment_id": assignment.assignment_id,
                    "worker_session_id": "child-retain",
                },
            )

            result = backend.finalize_assignment(
                worktree.project,
                "session-retain",
                {
                    "assignment_id": assignment.assignment_id,
                    "worker_session_id": "child-retain",
                    "worker_message": "缺少規格",
                    "worker_error": "",
                    "cancelled": False,
                },
            )

        self.assertEqual(result["status"], "worker-finished-without-validation")
        self.assertTrue(result["worktree_retained"])
        self.assertEqual(Path(result["worktree"]), worktree.project)

    def test_post_worker_verification_rejects_changes_after_publish(self) -> None:
        worktree, assignment = self.prepare("session-post-publish", "AlphaService")
        self.write_candidate(worktree, assignment, "AlphaService")
        result = {
            "status": "draft-pr-created",
            "target_class": assignment.target_class,
            "test_file": assignment.test_file,
            "branch": assignment.branch,
            "base_sha": assignment.base.head_sha,
            "pr_created": True,
            "pr_verified": True,
            "pr": {"draft": True, "url": "https://github.com/example/repository/pull/7"},
            "commit_sha": self.base_sha,
            "remote_sha": self.base_sha,
        }
        with (
            patch.object(backend, "verify_assignment_state"),
            self.assertRaisesRegex(backend.RequestError, "發布後又留下未提交變更"),
        ):
            backend.verify_completed_worker(worktree, assignment, result)

    def test_backend_rejects_changes_outside_assigned_test_file(self) -> None:
        worktree, assignment = self.prepare("session-scope", "AlphaService")
        self.write_candidate(worktree, assignment, "AlphaService")
        extra = worktree.project / "src/test/java/com/example/ExtraTest.java"
        extra.write_text("class ExtraTest {}\n", encoding="utf-8")
        with self.assertRaisesRegex(backend.RequestError, "必須只有"):
            backend.validate_candidate_request(worktree.project, assignment, {}, require_cases=False)

    def test_validate_passes_without_committing(self) -> None:
        worktree, assignment = self.prepare("session-validate", "AlphaService")
        request = self.write_candidate(worktree, assignment, "AlphaService")
        with (
            patch.object(backend, "verify_assignment_state"),
            patch.object(backend, "run_maven", side_effect=self.maven_success),
            patch.object(backend, "test_summary", side_effect=self.summary_success),
            patch.object(backend, "coverage_summary", side_effect=self.coverage_success),
        ):
            result = backend.validate_action(worktree.project, assignment, request)

        self.assertEqual(result["status"], "validation-passed")
        self.assertEqual(result["validation"]["coverage"]["percent"], 100.0)
        self.assertEqual(checked("rev-parse", "HEAD", cwd=worktree.project), self.base_sha)
        self.assertEqual(backend.changed_paths(worktree.project), {assignment.test_file})
        self.assertFalse(result["submitted"])
        self.assertFalse(result["pr_created"])
        self.assertEqual(
            result["candidate_sha256"],
            backend.hashlib.sha256(request["file"]["content"].encode("utf-8")).hexdigest(),
        )
        saved_result = json.loads(assignment.result_path.read_text(encoding="utf-8"))
        self.assertEqual(saved_result, result)

    def test_finalize_validated_result_retains_local_worktree(self) -> None:
        worktree, assignment = self.prepare("session-local-validation", "AlphaService")
        request = self.write_candidate(worktree, assignment, "AlphaService")
        with (
            patch.object(backend, "verify_assignment_state"),
            patch.object(backend, "run_maven", side_effect=self.maven_success),
            patch.object(backend, "test_summary", side_effect=self.summary_success),
            patch.object(backend, "coverage_summary", side_effect=self.coverage_success),
        ):
            validated = backend.validate_action(worktree.project, assignment, request)
            backend.bind_assignment(
                worktree.project,
                "session-local-validation",
                {
                    "assignment_id": assignment.assignment_id,
                    "worker_session_id": "child-local-validation",
                },
            )

        with patch.object(backend, "verify_assignment_state"):
            result = backend.finalize_assignment(
                worktree.project,
                "session-local-validation",
                {
                    "assignment_id": assignment.assignment_id,
                    "worker_session_id": "child-local-validation",
                    "worker_message": "本地驗證完成",
                    "worker_error": "",
                    "cancelled": False,
                },
            )

        self.assertEqual(validated["status"], "validation-passed")
        self.assertEqual(result["status"], "validation-passed")
        self.assertTrue(result["post_worker_verified"])
        self.assertTrue(result["worktree_retained"])
        self.assertEqual(Path(result["worktree"]), worktree.project)
        self.assertEqual(checked("rev-parse", "HEAD", cwd=worktree.project), self.base_sha)
        self.assertEqual(backend.changed_paths(worktree.project), {assignment.test_file})
        self.assertIsNone(
            backend.remote_sha(worktree.project, assignment.base.remote, assignment.branch)
        )

    def test_finalize_rejects_candidate_changed_after_validation(self) -> None:
        worktree, assignment = self.prepare("session-stale-validation", "AlphaService")
        request = self.write_candidate(worktree, assignment, "AlphaService")
        with (
            patch.object(backend, "verify_assignment_state"),
            patch.object(backend, "run_maven", side_effect=self.maven_success),
            patch.object(backend, "test_summary", side_effect=self.summary_success),
            patch.object(backend, "coverage_summary", side_effect=self.coverage_success),
        ):
            backend.validate_action(worktree.project, assignment, request)
            backend.bind_assignment(
                worktree.project,
                "session-stale-validation",
                {
                    "assignment_id": assignment.assignment_id,
                    "worker_session_id": "child-stale-validation",
                },
            )
        candidate = worktree.project / assignment.test_file
        candidate.write_text(candidate.read_text(encoding="utf-8") + "// 驗證後修改\n", encoding="utf-8")

        with patch.object(backend, "verify_assignment_state"):
            result = backend.finalize_assignment(
                worktree.project,
                "session-stale-validation",
                {
                    "assignment_id": assignment.assignment_id,
                    "worker_session_id": "child-stale-validation",
                    "worker_message": "完成",
                    "worker_error": "",
                    "cancelled": False,
                },
            )

        self.assertEqual(result["status"], "post-worker-verification-failed")
        self.assertIn("最新一次驗證後又修改", result["message"])
        self.assertTrue(result["worktree_retained"])

    def test_maven_failure_returns_only_error_diagnostics(self) -> None:
        worktree, assignment = self.prepare("session-maven-fail", "AlphaService")
        request = self.write_candidate(worktree, assignment, "AlphaService")
        failure = {
            "command": "./mvnw -B -ntp -Dtest=com.example.AlphaServiceTest test",
            "exit_code": 1,
            "timed_out": False,
            "maven_errors": "[ERROR] cannot find symbol",
        }
        with (
            patch.object(backend, "verify_assignment_state"),
            patch.object(backend, "run_maven", return_value=failure),
        ):
            result = backend.validate_action(worktree.project, assignment, request)

        self.assertEqual(result["status"], "candidate-check-failed")
        self.assertEqual(result["maven_errors"], "[ERROR] cannot find symbol")
        self.assertFalse(result["submitted"])

    def test_maven_tracked_side_effect_blocks_validation(self) -> None:
        worktree, assignment = self.prepare("session-side-effect", "AlphaService")
        request = self.write_candidate(worktree, assignment, "AlphaService")

        def mutate_project(project: Path, candidate_class: str) -> dict[str, object]:
            (project / "pom.xml").write_text("<project>changed</project>\n", encoding="utf-8")
            return self.maven_success(project, candidate_class)

        with (
            patch.object(backend, "verify_assignment_state"),
            patch.object(backend, "run_maven", side_effect=mutate_project),
        ):
            result = backend.validate_action(worktree.project, assignment, request)

        self.assertEqual(result["status"], "validation-failed")
        self.assertIn("pom.xml", result["message"])

    def test_maven_cannot_change_candidate_after_validation(self) -> None:
        worktree, assignment = self.prepare("session-candidate-mutation", "AlphaService")
        request = self.write_candidate(worktree, assignment, "AlphaService")

        def mutate_candidate(project: Path, candidate_class: str) -> dict[str, object]:
            path = project / assignment.test_file
            path.write_text(path.read_text(encoding="utf-8") + "// changed\n", encoding="utf-8")
            return self.maven_success(project, candidate_class)

        with (
            patch.object(backend, "verify_assignment_state"),
            patch.object(backend, "run_maven", side_effect=mutate_candidate),
        ):
            result = backend.validate_action(worktree.project, assignment, request)

        self.assertEqual(result["status"], "validation-failed")
        self.assertIn("驗證前不一致", result["message"])

    def test_success_commits_only_assigned_test_and_keeps_main_unchanged(self) -> None:
        worktree, assignment = self.prepare("session-submit", "AlphaService")
        request = self.write_candidate(worktree, assignment, "AlphaService")
        result = self.submit_success(worktree, assignment, request)

        self.assertEqual(result["status"], "draft-pr-created")
        self.assertTrue(result["pr_created"])
        self.assertTrue(result["pr_verified"])
        self.assertEqual(result["commit_sha"], result["remote_sha"])
        self.assertEqual(checked("rev-parse", "HEAD", cwd=self.repo), self.base_sha)
        self.assertEqual(checked("rev-parse", "refs/heads/main", git_dir=self.remote), self.base_sha)
        branch = str(result["branch"])
        head_sha = str(result["commit_sha"])
        self.assertEqual(checked("rev-parse", f"refs/heads/{branch}", git_dir=self.remote), head_sha)
        self.assertEqual(checked("rev-parse", f"{head_sha}^", git_dir=self.remote), self.base_sha)
        paths = checked("diff-tree", "--no-commit-id", "--name-only", "-r", head_sha, git_dir=self.remote)
        self.assertEqual(paths, assignment.test_file)
        stored = checked("show", f"{head_sha}:{assignment.test_file}", git_dir=self.remote)
        self.assertEqual(stored + "\n", request["file"]["content"])
        saved_result = json.loads(assignment.result_path.read_text(encoding="utf-8"))
        self.assertEqual(saved_result["assignment_id"], assignment.assignment_id)

    def test_remote_base_move_after_maven_prevents_commit_and_push(self) -> None:
        worktree, assignment = self.prepare("session-stale-base", "AlphaService")
        request = self.write_candidate(worktree, assignment, "AlphaService")

        def move_remote_main(project: Path, candidate_class: str) -> dict[str, object]:
            tree = checked("show", "-s", "--format=%T", self.base_sha, cwd=self.repo)
            moved = checked("commit-tree", tree, "-p", self.base_sha, "-m", "remote moved", cwd=self.repo)
            checked("push", "origin", f"{moved}:refs/heads/main", cwd=self.repo)
            return self.maven_success(project, candidate_class)

        with (
            patch.object(backend, "verify_assignment_state"),
            patch.object(backend, "run_maven", side_effect=move_remote_main),
            patch.object(backend, "test_summary", side_effect=self.summary_success),
            patch.object(backend, "coverage_summary", side_effect=self.coverage_success),
            patch.object(backend, "push_branch") as pushed,
        ):
            result = backend.submit(worktree.project, assignment, request)

        self.assertEqual(result["status"], "submission-failed")
        self.assertIn("Maven 驗證後", result["message"])
        self.assertIn("已移動", result["message"])
        pushed.assert_not_called()
        self.assertEqual(checked("rev-parse", "HEAD", cwd=worktree.project), self.base_sha)

    def test_push_error_reconciles_remote_sha(self) -> None:
        worktree, assignment = self.prepare("session-push-timeout", "AlphaService")
        request = self.write_candidate(worktree, assignment, "AlphaService")
        real_push = backend.push_branch

        def push_then_timeout(project: Path, base: object, branch: str) -> None:
            real_push(project, base, branch)
            raise backend.RequestError("push response timed out")

        with (
            patch.object(backend, "verify_assignment_state"),
            patch.object(backend, "run_maven", side_effect=self.maven_success),
            patch.object(backend, "test_summary", side_effect=self.summary_success),
            patch.object(backend, "coverage_summary", side_effect=self.coverage_success),
            patch.object(backend, "push_branch", side_effect=push_then_timeout),
            patch.object(backend, "create_draft_pr") as created,
        ):
            result = backend.submit(worktree.project, assignment, request)

        self.assertEqual(result["status"], "push-failed")
        self.assertTrue(result["submitted"])
        self.assertEqual(result["remote_sha"], result["commit_sha"])
        self.assertEqual(result["remote_state"], "verified-after-push-attempt")
        self.assertTrue(result["manual_recovery_required"])
        created.assert_not_called()

    def test_cancellation_during_push_reports_unknown_remote_state(self) -> None:
        worktree, assignment = self.prepare("session-cancel-push", "AlphaService")
        request = self.write_candidate(worktree, assignment, "AlphaService")

        def cancel_push(_project: Path, _base: object, _branch: str) -> None:
            backend._CANCEL_REQUESTED = True
            raise backend.RequestError("工作已取消")

        try:
            with (
                patch.object(backend, "verify_assignment_state"),
                patch.object(backend, "run_maven", side_effect=self.maven_success),
                patch.object(backend, "test_summary", side_effect=self.summary_success),
                patch.object(backend, "coverage_summary", side_effect=self.coverage_success),
                patch.object(backend, "push_branch", side_effect=cancel_push),
            ):
                result = backend.submit(worktree.project, assignment, request)
        finally:
            backend._CANCEL_REQUESTED = False

        self.assertEqual(result["status"], "cancelled")
        self.assertIsNone(result["submitted"])
        self.assertEqual(result["remote_state"], "unknown")
        self.assertTrue(result["manual_recovery_required"])


class PureFunctionAndCommandTest(unittest.TestCase):
    def test_branch_name_uses_session_target_and_base(self) -> None:
        base = "a" * 40
        first = backend.branch_name("session-one", "com.example.AlphaService", base)
        second = backend.branch_name("session-two", "com.example.AlphaService", base)
        self.assertNotEqual(first, second)
        self.assertEqual(first, backend.branch_name("session-one", "com.example.AlphaService", base))
        self.assertTrue(first.startswith("opencode/unit-test/alphaservice-"))

    def test_github_remote_parses_https_and_ssh(self) -> None:
        expected = ("github.com", "owner/repository")
        self.assertEqual(backend.github_remote("https://github.com/owner/repository.git"), expected)
        self.assertEqual(backend.github_remote("git@github.com:owner/repository.git"), expected)

    def test_validate_target_requires_service_suffix(self) -> None:
        with tempfile.TemporaryDirectory(prefix="target-validation-") as temporary:
            repo = Path(temporary)
            with self.assertRaisesRegex(backend.RequestError, "Service 結尾"):
                backend.validate_target(repo, "com.example.Alpha")

    def test_coverage_requires_jacoco_execution_data(self) -> None:
        with tempfile.TemporaryDirectory(prefix="coverage-artifacts-") as temporary:
            project = Path(temporary)
            report = project / "target/site/jacoco/jacoco.xml"
            report.parent.mkdir(parents=True)
            report.write_text("<report/>\n", encoding="utf-8")
            with self.assertRaisesRegex(backend.RequestError, "target/jacoco.exec"):
                backend.coverage_summary(project, "com.example.AlphaService")

    def test_maven_environment_removes_git_github_and_ssh_credentials(self) -> None:
        captured: dict[str, str] = {}

        def fake_run(invocation: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            captured.update(kwargs["env"])
            return subprocess.CompletedProcess(
                invocation,
                1,
                "[INFO] Compiling tests\n[ERROR] AlphaTest.java:[12,9] cannot find symbol\n",
                "WARNING: JVM option is deprecated\n[ERROR] -> [Help 1]\n",
            )

        with tempfile.TemporaryDirectory(prefix="maven-environment-") as temporary:
            project = Path(temporary) / "project"
            project.mkdir()
            with (
                patch.dict(
                    backend.os.environ,
                    {
                        "GH_TOKEN": "secret-gh",
                        "GITHUB_TOKEN": "secret-github",
                        "GIT_ASKPASS": "/secret/askpass",
                        "SSH_AUTH_SOCK": "/secret/agent.sock",
                    },
                ),
                patch.object(backend, "run_command", side_effect=fake_run),
            ):
                result = backend.run_maven(project, "com.example.AlphaServiceTest")

        self.assertEqual(result["exit_code"], 1)
        self.assertEqual(
            result["maven_errors"],
            "[ERROR] AlphaTest.java:[12,9] cannot find symbol\n[ERROR] -> [Help 1]",
        )
        self.assertNotIn("GH_TOKEN", captured)
        self.assertNotIn("GITHUB_TOKEN", captured)
        self.assertNotIn("GIT_ASKPASS", captured)
        self.assertNotIn("SSH_AUTH_SOCK", captured)
        self.assertEqual(captured["GIT_CONFIG_GLOBAL"], backend.os.devnull)
        self.assertEqual(captured["GIT_CONFIG_NOSYSTEM"], "1")
        self.assertEqual(captured["GIT_TERMINAL_PROMPT"], "0")

    def test_create_command_is_draft_and_never_merges(self) -> None:
        base = backend.BaseContext(
            branch="main",
            head_sha="a" * 40,
            remote="origin",
            remote_branch="main",
            github_host="github.com",
            github_repository="example/repository",
        )
        assignment = backend.Assignment(
            assignment_id="id",
            coordinator_session_id="session",
            worker_session_id="child-session",
            coordinator_repo=Path.cwd(),
            common_git_dir=Path.cwd(),
            manifest_path=Path.cwd() / "assignment.json",
            result_path=Path.cwd() / "result.json",
            branch="opencode/unit-test/alpha-123456789abc",
            target_class="com.example.AlphaService",
            target_source="src/main/java/com/example/AlphaService.java",
            candidate_class="com.example.AlphaServiceTest",
            test_file="src/test/java/com/example/AlphaServiceTest.java",
            specification_sources=("docs/service.md",),
            base=base,
        )
        request = {
            "test_cases": [
                {"id": "UT-001", "scenario": "情境", "expected": "結果", "evidence": "docs/service.md"}
            ]
        }
        validation = {
            "command": "./mvnw -B -ntp -Dtest=com.example.AlphaServiceTest test",
            "candidate_tests": {"executed": 1},
            "coverage": {"percent": 100.0, "minimum_percent": 80},
        }
        head_sha = "b" * 40
        commands: list[list[str]] = []

        def fake_run(invocation: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            commands.append(invocation)
            if invocation[:3] == ["gh", "pr", "create"]:
                return subprocess.CompletedProcess(invocation, 0, "https://github.com/example/repository/pull/7\n", "")
            details = {
                "number": 7,
                "url": "https://github.com/example/repository/pull/7",
                "isDraft": True,
                "state": "OPEN",
                "headRefName": assignment.branch,
                "headRefOid": head_sha,
                "baseRefName": "main",
            }
            return subprocess.CompletedProcess(invocation, 0, json.dumps(details), "")

        with patch.object(backend, "run_command", side_effect=fake_run):
            result = backend.create_draft_pr(
                Path.cwd(),
                request,
                validation,
                assignment,
                head_sha,
                "c" * 64,
            )

        self.assertTrue(result["isDraft"])
        create = commands[0]
        self.assertIn("--draft", create)
        self.assertEqual(create[create.index("--base") + 1], "main")
        self.assertEqual(create[create.index("--head") + 1], assignment.branch)
        joined = " ".join(" ".join(invocation) for invocation in commands)
        self.assertNotIn(" pr merge ", f" {joined} ")
        self.assertNotIn(" pr ready ", f" {joined} ")
        self.assertNotIn(" push --force ", f" {joined} ")


if __name__ == "__main__":
    unittest.main()
