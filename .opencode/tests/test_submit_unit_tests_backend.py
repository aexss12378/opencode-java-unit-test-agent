from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
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


class SubmissionRepositoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="submit-unit-tests-")
        self.root = Path(self.temporary.name)
        self.remote = self.root / "remote.git"
        self.repo = self.root / "repo"
        checked("init", "--bare", str(self.remote))
        checked("init", "-b", "main", str(self.repo))
        checked("config", "user.name", "Unit Test Tool", cwd=self.repo)
        checked("config", "user.email", "unit-test-tool@example.invalid", cwd=self.repo)

        (self.repo / "pom.xml").write_text("<project/>\n", encoding="utf-8")
        wrapper = self.repo / "mvnw"
        wrapper.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        wrapper.chmod(0o755)
        source = self.repo / "src/main/java/com/example"
        source.mkdir(parents=True)
        (source / "Alpha.java").write_text("package com.example; public class Alpha {}\n", encoding="utf-8")
        (source / "Beta.java").write_text("package com.example; public class Beta {}\n", encoding="utf-8")
        for index in range(12):
            name = f"Service{index:02d}"
            (source / f"{name}.java").write_text(
                f"package com.example; public class {name} {{}}\n",
                encoding="utf-8",
            )
        checked("add", "--", "pom.xml", "mvnw", "src/main/java", cwd=self.repo)
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

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def request(simple_name: str) -> dict[str, object]:
        target = f"com.example.{simple_name}"
        test_class = f"{simple_name}Test"
        content = (
            "package com.example;\n\n"
            f"class {test_class} {{\n"
            "    // UT-001\n"
            "}\n"
        )
        return {
            "target_class": target,
            "candidate_class": f"com.example.{test_class}",
            "test_cases": [
                {
                    "id": "UT-001",
                    "scenario": "建立物件",
                    "expected": "建立成功",
                    "evidence": f"src/main/java/com/example/{simple_name}.java",
                }
            ],
            "file": {
                "path": f"src/test/java/com/example/{test_class}.java",
                "content": content,
            },
        }

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
        }

    @staticmethod
    def draft_pr(
        _project: Path,
        _request: dict[str, object],
        _validation: dict[str, object],
        base: object,
        branch: str,
        head_sha: str,
        _digest: str,
    ) -> dict[str, object]:
        return {
            "number": 7,
            "url": f"https://github.com/example/repository/pull/{head_sha[:6]}",
            "isDraft": True,
            "state": "OPEN",
            "headRefName": branch,
            "headRefOid": head_sha,
            "baseRefName": base.remote_branch,
        }

    def submit(self, session_id: str, request: dict[str, object]) -> dict[str, object]:
        with (
            patch.object(backend, "base_context", return_value=self.base),
            patch.object(backend, "run_maven", side_effect=self.maven_success),
            patch.object(backend, "test_summary", side_effect=self.summary_success),
            patch.object(backend, "coverage_summary", side_effect=self.coverage_success),
            patch.object(backend, "create_draft_pr", side_effect=self.draft_pr),
        ):
            return backend.submit(self.repo, session_id, request)

    def test_success_commits_only_candidate_and_keeps_main_unchanged(self) -> None:
        request = self.request("Alpha")
        result = self.submit("session-alpha", request)

        self.assertEqual(result["status"], "draft-pr-created")
        self.assertTrue(result["pr_created"])
        self.assertFalse(result["merged"])
        self.assertEqual(checked("rev-parse", "HEAD", cwd=self.repo), self.base_sha)
        self.assertEqual(checked("rev-parse", "refs/heads/main", git_dir=self.remote), self.base_sha)

        branch = str(result["branch"])
        head_sha = str(result["commit_sha"])
        self.assertEqual(checked("rev-parse", f"refs/heads/{branch}", git_dir=self.remote), head_sha)
        self.assertEqual(checked("rev-parse", f"{head_sha}^", git_dir=self.remote), self.base_sha)
        paths = checked("diff-tree", "--no-commit-id", "--name-only", "-r", head_sha, git_dir=self.remote)
        self.assertEqual(paths, request["file"]["path"])
        stored = checked("show", f"{head_sha}:{request['file']['path']}", git_dir=self.remote)
        self.assertEqual(stored + "\n", request["file"]["content"])
        self.assertEqual(checked("branch", "--list", branch, cwd=self.repo), "")
        self.assertEqual(checked("worktree", "list", "--porcelain", cwd=self.repo).count("worktree "), 1)

    def test_base_context_rejects_feature_branch(self) -> None:
        checked("switch", "-c", "feature/not-main", cwd=self.repo)
        try:
            with self.assertRaisesRegex(backend.RequestError, "必須從受信任基準分支 main 啟動"):
                backend.base_context(self.repo)
        finally:
            checked("switch", "main", cwd=self.repo)
            checked("branch", "-D", "feature/not-main", cwd=self.repo)

    def test_maven_failure_does_not_commit_push_or_create_pr(self) -> None:
        request = self.request("Alpha")
        failed = {
            "command": "./mvnw -B -ntp -Dtest=com.example.AlphaTest test",
            "exit_code": 1,
            "timed_out": False,
            "maven_errors": "[ERROR] COMPILATION ERROR",
        }
        with (
            patch.object(backend, "base_context", return_value=self.base),
            patch.object(backend, "run_maven", return_value=failed),
            patch.object(backend, "push_branch") as pushed,
            patch.object(backend, "create_draft_pr") as created,
        ):
            result = backend.submit(self.repo, "session-failed", request)

        self.assertEqual(result["status"], "candidate-check-failed")
        self.assertEqual(result["maven_errors"], "[ERROR] COMPILATION ERROR")
        self.assertEqual(result["diagnostic_field"], "maven_errors")
        self.assertIn("修正候選內容後重新提交", result["agent_action"])
        pushed.assert_not_called()
        created.assert_not_called()
        heads = checked("for-each-ref", "--format=%(refname:short)", "refs/heads", git_dir=self.remote)
        self.assertEqual(heads, "main")
        self.assertEqual(checked("rev-parse", "HEAD", cwd=self.repo), self.base_sha)
        self.assertEqual(checked("worktree", "list", "--porcelain", cwd=self.repo).count("worktree "), 1)

    def test_same_agent_can_fix_compile_error_and_resubmit(self) -> None:
        request = self.request("Alpha")
        failed = {
            "command": "./mvnw -B -ntp -Dtest=com.example.AlphaTest test",
            "exit_code": 1,
            "timed_out": False,
            "maven_errors": "[ERROR] cannot find symbol: assertThat",
        }
        with (
            patch.object(backend, "base_context", return_value=self.base),
            patch.object(backend, "run_maven", return_value=failed),
        ):
            first = backend.submit(self.repo, "session-retry", request)

        request["file"]["content"] = str(request["file"]["content"]).replace(
            "    // UT-001\n",
            "    // UT-001\n    // corrected import and assertion\n",
        )
        second = self.submit("session-retry", request)

        self.assertEqual(first["status"], "candidate-check-failed")
        self.assertIn("cannot find symbol", first["maven_errors"])
        self.assertEqual(second["status"], "draft-pr-created")
        self.assertNotEqual(first["branch"], second["branch"])

    def test_maven_side_effect_is_discarded_with_validation_copy(self) -> None:
        request = self.request("Alpha")

        def mutate_project(project: Path, candidate_class: str) -> dict[str, object]:
            self.assertFalse((project / ".git").exists())
            (project / "pom.xml").write_text("<project>changed</project>\n", encoding="utf-8")
            return self.maven_success(project, candidate_class)

        with (
            patch.object(backend, "base_context", return_value=self.base),
            patch.object(backend, "run_maven", side_effect=mutate_project),
            patch.object(backend, "test_summary", side_effect=self.summary_success),
            patch.object(backend, "coverage_summary", side_effect=self.coverage_success),
            patch.object(backend, "create_draft_pr", side_effect=self.draft_pr),
        ):
            result = backend.submit(self.repo, "session-side-effect", request)

        self.assertEqual(result["status"], "draft-pr-created")
        head_sha = str(result["commit_sha"])
        paths = checked("diff-tree", "--no-commit-id", "--name-only", "-r", head_sha, git_dir=self.remote)
        self.assertEqual(paths, request["file"]["path"])
        pom = checked("show", f"{head_sha}:pom.xml", git_dir=self.remote)
        self.assertEqual(pom, "<project/>")
        self.assertEqual(checked("rev-parse", "HEAD", cwd=self.repo), self.base_sha)

    def test_maven_cannot_change_candidate_after_validation(self) -> None:
        request = self.request("Alpha")

        def mutate_candidate(project: Path, candidate_class: str) -> dict[str, object]:
            path = project / str(request["file"]["path"])
            path.write_text(str(request["file"]["content"]) + "// changed\n", encoding="utf-8")
            return self.maven_success(project, candidate_class)

        with (
            patch.object(backend, "base_context", return_value=self.base),
            patch.object(backend, "run_maven", side_effect=mutate_candidate),
            patch.object(backend, "test_summary", side_effect=self.summary_success),
            patch.object(backend, "coverage_summary", side_effect=self.coverage_success),
            patch.object(backend, "push_branch") as pushed,
            patch.object(backend, "create_draft_pr") as created,
        ):
            result = backend.submit(self.repo, "session-mutated-candidate", request)

        self.assertEqual(result["status"], "submission-failed")
        self.assertIn("候選測試內容與提交內容不一致", result["message"])
        pushed.assert_not_called()
        created.assert_not_called()

    def test_remote_base_move_after_maven_prevents_push(self) -> None:
        request = self.request("Alpha")

        def move_remote_main(project: Path, candidate_class: str) -> dict[str, object]:
            tree = checked("show", "-s", "--format=%T", self.base_sha, cwd=self.repo)
            moved = checked("commit-tree", tree, "-p", self.base_sha, "-m", "remote moved", cwd=self.repo)
            checked("push", "origin", f"{moved}:refs/heads/main", cwd=self.repo)
            return self.maven_success(project, candidate_class)

        with (
            patch.object(backend, "base_context", return_value=self.base),
            patch.object(backend, "run_maven", side_effect=move_remote_main),
            patch.object(backend, "test_summary", side_effect=self.summary_success),
            patch.object(backend, "coverage_summary", side_effect=self.coverage_success),
            patch.object(backend, "push_branch") as pushed,
            patch.object(backend, "create_draft_pr") as created,
        ):
            result = backend.submit(self.repo, "session-stale-base", request)

        self.assertEqual(result["status"], "submission-failed")
        self.assertIn("Maven 驗證後", result["message"])
        self.assertIn("已移動", result["message"])
        pushed.assert_not_called()
        created.assert_not_called()

    def test_cancellation_during_push_reports_unknown_remote_state(self) -> None:
        request = self.request("Alpha")

        def cancel_push(_project: Path, _base: object, _branch: str) -> None:
            backend._CANCEL_REQUESTED = True
            raise backend.RequestError("工作已取消")

        try:
            with (
                patch.object(backend, "base_context", return_value=self.base),
                patch.object(backend, "run_maven", side_effect=self.maven_success),
                patch.object(backend, "test_summary", side_effect=self.summary_success),
                patch.object(backend, "coverage_summary", side_effect=self.coverage_success),
                patch.object(backend, "push_branch", side_effect=cancel_push),
                patch.object(backend, "create_draft_pr") as created,
            ):
                result = backend.submit(self.repo, "session-cancel-push", request)
        finally:
            backend._CANCEL_REQUESTED = False

        self.assertEqual(result["status"], "cancelled")
        self.assertIsNone(result["submitted"])
        self.assertEqual(result["remote_state"], "unknown")
        self.assertTrue(result["manual_recovery_required"])
        self.assertFalse(result["automatic_retry_supported"])
        created.assert_not_called()
        self.assertEqual(checked("branch", "--list", str(result["branch"]), cwd=self.repo), result["branch"])

        with patch.object(backend, "base_context", return_value=self.base):
            retry = backend.submit(self.repo, "session-cancel-push", request)
        self.assertEqual(retry["status"], "branch-conflict")
        self.assertTrue(retry["manual_recovery_required"])
        self.assertFalse(retry["automatic_retry_supported"])

    def test_push_error_reconciles_remote_branch_before_reporting(self) -> None:
        request = self.request("Alpha")
        real_push = backend.push_branch

        def push_then_timeout(project: Path, base: object, branch: str) -> None:
            real_push(project, base, branch)
            raise backend.RequestError("push response timed out")

        with (
            patch.object(backend, "base_context", return_value=self.base),
            patch.object(backend, "run_maven", side_effect=self.maven_success),
            patch.object(backend, "test_summary", side_effect=self.summary_success),
            patch.object(backend, "coverage_summary", side_effect=self.coverage_success),
            patch.object(backend, "push_branch", side_effect=push_then_timeout),
            patch.object(backend, "create_draft_pr") as created,
        ):
            result = backend.submit(self.repo, "session-push-timeout", request)

        self.assertEqual(result["status"], "push-failed")
        self.assertTrue(result["submitted"])
        self.assertEqual(result["remote_sha"], result["commit_sha"])
        self.assertEqual(result["remote_state"], "verified-after-push-attempt")
        self.assertTrue(result["manual_recovery_required"])
        self.assertIn("compare_url", result)
        created.assert_not_called()

    def test_pr_failure_reports_observed_remote_sha_instead_of_local_sha(self) -> None:
        request = self.request("Alpha")
        observed: dict[str, str] = {}

        def drift_after_pr(
            project: Path,
            submitted_request: dict[str, object],
            validation: dict[str, object],
            base: object,
            branch: str,
            head_sha: str,
            digest: str,
        ) -> dict[str, object]:
            pr = self.draft_pr(project, submitted_request, validation, base, branch, head_sha, digest)
            checked("config", "user.name", "Remote Drift", git_dir=self.remote)
            checked("config", "user.email", "drift@example.invalid", git_dir=self.remote)
            tree = checked("show", "-s", "--format=%T", head_sha, git_dir=self.remote)
            moved = checked("commit-tree", tree, "-p", head_sha, "-m", "remote drift", git_dir=self.remote)
            checked("update-ref", f"refs/heads/{branch}", moved, head_sha, git_dir=self.remote)
            observed["sha"] = moved
            return pr

        with (
            patch.object(backend, "base_context", return_value=self.base),
            patch.object(backend, "run_maven", side_effect=self.maven_success),
            patch.object(backend, "test_summary", side_effect=self.summary_success),
            patch.object(backend, "coverage_summary", side_effect=self.coverage_success),
            patch.object(backend, "create_draft_pr", side_effect=drift_after_pr),
        ):
            result = backend.submit(self.repo, "session-remote-drift", request)

        self.assertEqual(result["status"], "pr-create-or-verify-failed")
        self.assertEqual(result["remote_sha"], observed["sha"])
        self.assertNotEqual(result["remote_sha"], result["commit_sha"])
        self.assertTrue(result["pr_created"])
        self.assertFalse(result["pr_verified"])

    def test_twelve_parallel_submissions_use_distinct_worktrees_and_branches(self) -> None:
        requests = [self.request(f"Service{index:02d}") for index in range(12)]
        barrier = threading.Barrier(len(requests))
        worktrees: list[Path] = []
        lock = threading.Lock()

        def synchronized_maven(project: Path, candidate_class: str) -> dict[str, object]:
            with lock:
                worktrees.append(project)
            barrier.wait(timeout=10)
            return self.maven_success(project, candidate_class)

        with (
            patch.object(backend, "base_context", return_value=self.base),
            patch.object(backend, "run_maven", side_effect=synchronized_maven),
            patch.object(backend, "test_summary", side_effect=self.summary_success),
            patch.object(backend, "coverage_summary", side_effect=self.coverage_success),
            patch.object(backend, "create_draft_pr", side_effect=self.draft_pr),
            ThreadPoolExecutor(max_workers=len(requests)) as executor,
        ):
            futures = [
                executor.submit(backend.submit, self.repo, f"session-{index}", request)
                for index, request in enumerate(requests)
            ]
            results = [future.result(timeout=30) for future in futures]

        self.assertEqual({result["status"] for result in results}, {"draft-pr-created"})
        self.assertEqual(len(set(worktrees)), len(requests))
        self.assertEqual(len({result["branch"] for result in results}), len(requests))
        for result, request in zip(results, requests, strict=True):
            head_sha = str(result["commit_sha"])
            paths = checked("diff-tree", "--no-commit-id", "--name-only", "-r", head_sha, git_dir=self.remote)
            self.assertEqual(paths, request["file"]["path"])
        self.assertEqual(checked("rev-parse", "HEAD", cwd=self.repo), self.base_sha)
        self.assertEqual(checked("worktree", "list", "--porcelain", cwd=self.repo).count("worktree "), 1)


class DraftPullRequestCommandTest(unittest.TestCase):
    def test_create_command_is_draft_and_never_merges(self) -> None:
        base = backend.BaseContext(
            branch="main",
            head_sha="a" * 40,
            remote="origin",
            remote_branch="main",
            github_host="github.com",
            github_repository="example/repository",
        )
        request = SubmissionRepositoryTest.request("Alpha")
        validation = {
            "command": "./mvnw -B -ntp -Dtest=com.example.AlphaTest test",
            "candidate_tests": {"executed": 1},
            "coverage": {"percent": 100.0, "minimum_percent": 80},
        }
        branch = "codex/unit-test/alpha-123456789abc"
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
                "headRefName": branch,
                "headRefOid": head_sha,
                "baseRefName": "main",
            }
            return subprocess.CompletedProcess(invocation, 0, json.dumps(details), "")

        with patch.object(backend, "run_command", side_effect=fake_run):
            result = backend.create_draft_pr(
                Path.cwd(),
                request,
                validation,
                base,
                branch,
                head_sha,
                "c" * 64,
            )

        self.assertTrue(result["isDraft"])
        create = commands[0]
        self.assertIn("--draft", create)
        self.assertEqual(create[create.index("--base") + 1], "main")
        self.assertEqual(create[create.index("--head") + 1], branch)
        joined = " ".join(" ".join(invocation) for invocation in commands)
        self.assertNotIn(" pr merge ", f" {joined} ")
        self.assertNotIn(" pr ready ", f" {joined} ")
        self.assertNotIn(" push --force ", f" {joined} ")

    def test_create_timeout_reports_unknown_pr_state(self) -> None:
        base = backend.BaseContext(
            branch="main",
            head_sha="a" * 40,
            remote="origin",
            remote_branch="main",
            github_host="github.com",
            github_repository="example/repository",
        )
        request = SubmissionRepositoryTest.request("Alpha")
        validation = {
            "command": "./mvnw -B -ntp -Dtest=com.example.AlphaTest test",
            "candidate_tests": {"executed": 1},
            "coverage": {"percent": 100.0, "minimum_percent": 80},
        }

        def timed_out(invocation: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(invocation, 124, "", "request timed out")

        with (
            patch.object(backend, "run_command", side_effect=timed_out),
            self.assertRaises(backend.DraftPrStateUnknownError),
        ):
            backend.create_draft_pr(
                Path.cwd(),
                request,
                validation,
                base,
                "codex/unit-test/alpha-timeout",
                "b" * 40,
                "c" * 64,
            )


class PureFunctionTest(unittest.TestCase):
    def test_branch_name_uses_session_and_candidate_content(self) -> None:
        request = SubmissionRepositoryTest.request("Alpha")
        first = backend.branch_name("session-one", request)
        second = backend.branch_name("session-two", request)
        self.assertNotEqual(first, second)
        self.assertEqual(first, backend.branch_name("session-one", request))
        self.assertTrue(first.startswith("codex/unit-test/alpha-"))

    def test_github_remote_parses_https_and_ssh(self) -> None:
        expected = ("github.com", "owner/repository")
        self.assertEqual(backend.github_remote("https://github.com/owner/repository.git"), expected)
        self.assertEqual(backend.github_remote("git@github.com:owner/repository.git"), expected)

    def test_validation_copy_keeps_nested_target_package_but_removes_git_metadata(self) -> None:
        with tempfile.TemporaryDirectory(prefix="validation-copy-") as temporary:
            root = Path(temporary)
            source = root / "source"
            project = root / "project"
            (source / ".git").mkdir(parents=True)
            (source / ".git/config").write_text("secret\n", encoding="utf-8")
            (source / ".opencode").mkdir()
            (source / "target").mkdir()
            nested = source / "src/main/java/com/example/target"
            nested.mkdir(parents=True)
            (nested / "PriceUtil.java").write_text("class PriceUtil {}\n", encoding="utf-8")

            backend.create_validation_copy(source, project)

            self.assertFalse((project / ".git").exists())
            self.assertFalse((project / ".opencode").exists())
            self.assertFalse((project / "target").exists())
            self.assertTrue((project / "src/main/java/com/example/target/PriceUtil.java").is_file())

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
                result = backend.run_maven(project, "com.example.AlphaTest")

        self.assertEqual(result["exit_code"], 1)
        self.assertEqual(
            result["maven_errors"],
            "[ERROR] AlphaTest.java:[12,9] cannot find symbol\n[ERROR] -> [Help 1]",
        )
        self.assertNotIn("[INFO]", result["maven_errors"])
        self.assertNotIn("WARNING", result["maven_errors"])
        self.assertNotIn("GH_TOKEN", captured)
        self.assertNotIn("GITHUB_TOKEN", captured)
        self.assertNotIn("GIT_ASKPASS", captured)
        self.assertNotIn("SSH_AUTH_SOCK", captured)
        self.assertEqual(captured["GIT_CONFIG_GLOBAL"], backend.os.devnull)
        self.assertEqual(captured["GIT_CONFIG_NOSYSTEM"], "1")
        self.assertEqual(captured["GIT_TERMINAL_PROMPT"], "0")

    def test_maven_returns_all_error_lines_without_tail_truncation(self) -> None:
        error_lines = [f"[ERROR] compiler failure {index}" for index in range(250)]
        long_error = "[ERROR] " + ("x" * 5_000)

        def fake_run(invocation: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            stdout = "\n".join(["[INFO] Starting build", *error_lines, long_error])
            stderr = "[WARNING] This line must not be returned\n"
            return subprocess.CompletedProcess(invocation, 1, stdout, stderr)

        with tempfile.TemporaryDirectory(prefix="maven-errors-") as temporary:
            project = Path(temporary) / "project"
            project.mkdir()
            with patch.object(backend, "run_command", side_effect=fake_run):
                result = backend.run_maven(project, "com.example.AlphaTest")

        self.assertEqual(result["maven_errors"].splitlines(), [*error_lines, long_error])
        self.assertNotIn("failure_tail", result)


if __name__ == "__main__":
    unittest.main()
