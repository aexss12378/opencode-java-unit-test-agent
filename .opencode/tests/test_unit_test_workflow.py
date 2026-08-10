from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

TOOLS = Path(__file__).parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

import _unit_test_common as common
import prepare_unit_test_workspaces as prepare
import publish_unit_test as publish
import validate_unit_test as validate


def command(
    *arguments: str, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *arguments],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def checked(*arguments: str, cwd: Path | None = None) -> str:
    result = command(*arguments, cwd=cwd)
    if result.returncode != 0:
        raise RuntimeError(result.stdout + result.stderr)
    return result.stdout.strip()


class UnitTestWorkflowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="three-unit-test-tools-")
        self.root = Path(self.temporary.name)
        self.remote = self.root / "remote.git"
        self.repo = self.root / "repo"
        checked("init", "--bare", str(self.remote))
        checked("init", "-b", "main", str(self.repo))
        checked("config", "user.name", "Unit Test Tool", cwd=self.repo)
        checked("config", "user.email", "unit-test-tool@example.invalid", cwd=self.repo)
        (self.repo / ".gitignore").write_text(
            "target/\nunit-test-worktrees/\n", encoding="utf-8"
        )
        (self.repo / "pom.xml").write_text("<project/>\n", encoding="utf-8")
        wrapper = self.repo / "mvnw"
        wrapper.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        wrapper.chmod(0o755)
        source = self.repo / "src/main/java/com/example"
        source.mkdir(parents=True)
        docs = self.repo / "docs"
        docs.mkdir()
        for name in ("AlphaService", "BetaService"):
            (source / f"{name}.java").write_text(
                f"package com.example; public class {name} {{}}\n",
                encoding="utf-8",
            )
            (docs / f"{name}.md").write_text(f"# {name} 規格\n", encoding="utf-8")
        checked(
            "add",
            ".gitignore",
            "pom.xml",
            "mvnw",
            "src/main/java",
            "docs",
            cwd=self.repo,
        )
        checked("commit", "-m", "initial", cwd=self.repo)
        checked("remote", "add", "origin", str(self.remote), cwd=self.repo)
        checked("push", "-u", "origin", "main", cwd=self.repo)
        self.base_sha = checked("rev-parse", "HEAD", cwd=self.repo)
        self.base = common.BaseContext(
            branch="main",
            head_sha=self.base_sha,
            remote="origin",
            remote_branch="main",
            github_host="github.com",
            github_repository="example/repository",
        )
        self.prepared: list[dict[str, object]] = []

    def tearDown(self) -> None:
        common._CANCEL_REQUESTED = False
        for item in reversed(self.prepared):
            worktree = self.repo / str(item["worktree"])
            if worktree.exists():
                command("worktree", "remove", "--force", str(worktree), cwd=self.repo)
            command("branch", "-D", "--", str(item["branch"]), cwd=self.repo)
        self.temporary.cleanup()

    @staticmethod
    def target(name: str) -> dict[str, object]:
        return {
            "target_class": f"com.example.{name}",
            "specification_sources": [f"docs/{name}.md"],
        }

    def request(
        self,
        *,
        targets: list[dict[str, object]] | None = None,
        not_started: list[dict[str, str]] | None = None,
    ) -> dict[str, object]:
        return {
            "execution_mode": "unit-test-all/v2",
            "targets": targets
            if targets is not None
            else [self.target("AlphaService")],
            "not_started": not_started
            if not_started is not None
            else [
                {
                    "target_class": "com.example.BetaService",
                    "reason": "缺少可信規格證據",
                }
            ],
            "max_concurrency": 2,
        }

    @staticmethod
    def cases() -> list[dict[str, str]]:
        return [
            {
                "id": "UT-001",
                "scenario": "建立物件",
                "expected": "建立成功",
                "evidence": "docs/AlphaService.md",
            }
        ]

    @staticmethod
    def candidate_content(name: str) -> str:
        return f"package com.example;\n\nclass {name}Test {{\n    // UT-001\n}}\n"

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

    def prepare_all(self) -> dict[str, object]:
        request = prepare.validate_request(
            self.repo,
            self.request(
                targets=[self.target("AlphaService"), self.target("BetaService")],
                not_started=[],
            ),
        )
        with patch.object(prepare, "base_context", return_value=self.base):
            result = prepare.prepare(self.repo, "coordinator-1", request)
        self.prepared.extend(result["prepared"])
        return result

    def load_worker(
        self,
        item: dict[str, object],
        session: str = "worker-1",
        *,
        require_base_head: bool = True,
    ) -> common.Assignment:
        with patch.object(
            common,
            "github_remote",
            return_value=("github.com", "example/repository"),
        ):
            return common.load_assignment(
                self.repo,
                str(item["assignment_id"]),
                session,
                bind_worker=True,
                require_base_head=require_base_head,
            )

    def write_candidate(
        self, assignment: common.Assignment, name: str = "AlphaService"
    ) -> None:
        path = assignment.worktree / assignment.test_file
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.candidate_content(name), encoding="utf-8")

    def validate_candidate(self, assignment: common.Assignment) -> dict[str, object]:
        with (
            patch.object(validate, "run_maven", side_effect=self.maven_success),
            patch.object(validate, "test_summary", side_effect=self.summary_success),
            patch.object(
                validate, "coverage_summary", side_effect=self.coverage_success
            ),
            patch.object(
                common,
                "github_remote",
                return_value=("github.com", "example/repository"),
            ),
        ):
            return validate.validate(assignment, {"test_cases": self.cases()})

    def test_prepare_requires_complete_service_classification(self) -> None:
        incomplete = self.request(targets=[self.target("AlphaService")], not_started=[])
        with self.assertRaisesRegex(common.RequestError, "未分類.*BetaService"):
            prepare.validate_request(self.repo, incomplete)

    def test_prepare_creates_visible_worktree_and_one_branch_per_service(self) -> None:
        result = self.prepare_all()

        self.assertEqual(result["status"], "prepared")
        self.assertEqual(result["service_count"], 2)
        self.assertEqual(len(result["prepared"]), 2)
        for item in result["prepared"]:
            worktree = self.repo / item["worktree"]
            self.assertTrue(worktree.is_dir())
            self.assertTrue(str(item["worktree"]).startswith("unit-test-worktrees/"))
            self.assertEqual(
                checked("branch", "--show-current", cwd=worktree), item["branch"]
            )
            self.assertIn("assignment_id:", item["prompt"])
            self.assertIn("publish_unit_test", item["prompt"])
        self.assertEqual(checked("status", "--porcelain", cwd=self.repo), "")

    def test_prepare_accepts_every_service_as_not_started(self) -> None:
        request = prepare.validate_request(
            self.repo,
            self.request(
                targets=[],
                not_started=[
                    {
                        "target_class": "com.example.AlphaService",
                        "reason": "缺少可信規格證據",
                    },
                    {
                        "target_class": "com.example.BetaService",
                        "reason": "可信規格彼此衝突",
                    },
                ],
            ),
        )
        with patch.object(prepare, "base_context", return_value=self.base):
            result = prepare.prepare(self.repo, "coordinator-2", request)

        self.assertEqual(result["status"], "prepared")
        self.assertEqual(result["prepared"], [])
        self.assertEqual(len(result["results"]), 2)

    def test_validation_binds_exact_worker_and_writes_receipt(self) -> None:
        item = self.prepare_all()["prepared"][0]
        assignment = self.load_worker(item)
        self.write_candidate(assignment)

        result = self.validate_candidate(assignment)

        self.assertEqual(result["status"], "validation-passed")
        self.assertRegex(result["validation_id"], r"^[0-9a-f]{24}$")
        state = json.loads(assignment.state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["worker_session_id"], "worker-1")
        self.assertEqual(state["validation"]["validation_id"], result["validation_id"])
        with (
            patch.object(
                common,
                "github_remote",
                return_value=("github.com", "example/repository"),
            ),
            self.assertRaisesRegex(common.RequestError, "工作階段.*不一致"),
        ):
            common.load_assignment(
                self.repo,
                str(item["assignment_id"]),
                "worker-2",
                bind_worker=True,
            )

    def test_validation_rejects_changes_outside_assigned_test_file(self) -> None:
        item = self.prepare_all()["prepared"][0]
        assignment = self.load_worker(item)
        self.write_candidate(assignment)
        extra = assignment.worktree / "src/test/java/com/example/ExtraTest.java"
        extra.write_text("class ExtraTest {}\n", encoding="utf-8")

        with (
            patch.object(
                common,
                "github_remote",
                return_value=("github.com", "example/repository"),
            ),
            self.assertRaisesRegex(common.RequestError, "Git 變更必須只有"),
        ):
            validate.validate(assignment, {"test_cases": self.cases()})

    def test_validation_preserves_all_maven_error_lines(self) -> None:
        result = subprocess.CompletedProcess(
            ["mvnw"],
            1,
            "[INFO] start\n[ERROR] first\n",
            "[ERROR] second\nwarning\n",
        )
        with patch.object(validate, "run_command", return_value=result):
            output = validate.run_maven(self.repo, "com.example.AlphaServiceTest")

        self.assertEqual(output["maven_errors"], "[ERROR] first\n[ERROR] second")

    def test_maven_environment_excludes_git_github_and_ssh_credentials(self) -> None:
        with patch.dict(
            os.environ,
            {
                "GITHUB_TOKEN": "secret",
                "GH_TOKEN": "secret",
                "SSH_AUTH_SOCK": "/tmp/agent.sock",
                "PATH": "/usr/bin",
            },
            clear=True,
        ):
            environment = validate.maven_environment(self.repo)

        self.assertEqual(environment["PATH"], "/usr/bin")
        self.assertNotIn("GITHUB_TOKEN", environment)
        self.assertNotIn("GH_TOKEN", environment)
        self.assertNotIn("SSH_AUTH_SOCK", environment)
        self.assertEqual(environment["GIT_TERMINAL_PROMPT"], "0")

    def test_publish_commits_only_candidate_pushes_and_keeps_worktree(self) -> None:
        item = self.prepare_all()["prepared"][0]
        assignment = self.load_worker(item)
        self.write_candidate(assignment)
        validation_result = self.validate_candidate(assignment)
        assignment = self.load_worker(item)
        draft = {
            "number": 7,
            "url": "https://github.com/example/repository/pull/7",
            "isDraft": True,
            "state": "OPEN",
            "headRefName": assignment.branch,
            "headRefOid": "filled-by-test",
            "baseRefName": "main",
        }

        def fake_pr(
            _assignment: common.Assignment,
            _receipt: dict[str, object],
            commit_sha: str,
        ) -> dict[str, object]:
            return {**draft, "headRefOid": commit_sha}

        with (
            patch.object(
                common,
                "github_remote",
                return_value=("github.com", "example/repository"),
            ),
            patch.object(publish, "create_or_reconcile_pr", side_effect=fake_pr),
        ):
            result = publish.publish(assignment, validation_result["validation_id"])

        self.assertEqual(result["status"], "draft-pr-created")
        self.assertEqual(result["commit_sha"], result["remote_sha"])
        self.assertTrue(result["worktree_retained"])
        self.assertTrue(assignment.worktree.is_dir())
        self.assertEqual(checked("status", "--porcelain", cwd=assignment.worktree), "")
        changed = checked(
            "diff-tree",
            "--no-commit-id",
            "--name-only",
            "-r",
            "HEAD",
            cwd=assignment.worktree,
        )
        self.assertEqual(changed, assignment.test_file)
        remote_head = checked(
            "--git-dir",
            str(self.remote),
            "rev-parse",
            f"refs/heads/{assignment.branch}",
        )
        self.assertEqual(remote_head, result["commit_sha"])

    def test_publish_rejects_candidate_changed_after_validation(self) -> None:
        item = self.prepare_all()["prepared"][0]
        assignment = self.load_worker(item)
        self.write_candidate(assignment)
        validation_result = self.validate_candidate(assignment)
        path = assignment.worktree / assignment.test_file
        path.write_text(
            path.read_text(encoding="utf-8") + "// changed\n", encoding="utf-8"
        )
        assignment = self.load_worker(item)

        with self.assertRaisesRegex(common.RequestError, "驗證通過後又被修改"):
            publish.publish(assignment, validation_result["validation_id"])

    def test_publish_is_reentrant_after_verified_publication(self) -> None:
        item = self.prepare_all()["prepared"][0]
        assignment = self.load_worker(item)
        self.write_candidate(assignment)
        validation_result = self.validate_candidate(assignment)
        assignment = self.load_worker(item)

        def fake_pr(
            current: common.Assignment,
            _receipt: dict[str, object],
            commit_sha: str,
        ) -> dict[str, object]:
            return {
                "number": 8,
                "url": "https://github.com/example/repository/pull/8",
                "isDraft": True,
                "state": "OPEN",
                "headRefName": current.branch,
                "headRefOid": commit_sha,
                "baseRefName": "main",
            }

        with (
            patch.object(
                common,
                "github_remote",
                return_value=("github.com", "example/repository"),
            ),
            patch.object(publish, "create_or_reconcile_pr", side_effect=fake_pr),
        ):
            first = publish.publish(assignment, validation_result["validation_id"])
        reloaded = self.load_worker(item, require_base_head=False)
        pr_details = {
            "number": 8,
            "url": "https://github.com/example/repository/pull/8",
            "isDraft": True,
            "state": "OPEN",
            "headRefName": reloaded.branch,
            "headRefOid": first["commit_sha"],
            "baseRefName": "main",
        }
        with (
            patch.object(
                common,
                "github_remote",
                return_value=("github.com", "example/repository"),
            ),
            patch.object(publish, "list_existing_prs", return_value=[pr_details]),
        ):
            second = publish.publish(reloaded, validation_result["validation_id"])

        self.assertEqual(second["status"], "draft-pr-created")
        self.assertIn("重新核對", second["message"])
        self.assertEqual(first["commit_sha"], second["commit_sha"])


class PureContractTest(unittest.TestCase):
    def test_branch_name_is_stable_and_service_specific(self) -> None:
        first = common.branch_name("session-1", "com.example.AlphaService", "a" * 40)
        same = common.branch_name("session-1", "com.example.AlphaService", "a" * 40)
        other = common.branch_name("session-1", "com.example.BetaService", "a" * 40)

        self.assertEqual(first, same)
        self.assertNotEqual(first, other)
        self.assertTrue(first.startswith("opencode/unit-test/alphaservice-"))

    def test_pr_verification_requires_open_draft_and_exact_sha(self) -> None:
        source = Path(publish.__file__).read_text(encoding="utf-8")

        self.assertIn('"isDraft": True', source)
        self.assertIn('"state": "OPEN"', source)
        self.assertIn('"headRefOid": commit_sha', source)
        self.assertIn('"--draft"', source)
        self.assertNotIn('"merge"', source)
        self.assertNotIn('"ready"', source)


if __name__ == "__main__":
    unittest.main()
