from __future__ import annotations

import re
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
COMMAND = PROJECT_ROOT / ".opencode/commands/unit-test-all.md"
ORCHESTRATOR = PROJECT_ROOT / ".opencode/agents/unit-test-orchestrator.md"
UNIT_TEST_AGENT = PROJECT_ROOT / ".opencode/agents/unit-test.md"


def frontmatter(text: str) -> dict[str, str]:
    match = re.match(r"\A---\n(?P<body>.*?)\n---\n", text, re.DOTALL)
    if match is None:
        raise AssertionError("缺少有效的 Markdown frontmatter")
    result: dict[str, str] = {}
    for line in match.group("body").splitlines():
        key, separator, value = line.partition(":")
        if separator:
            result[key.strip()] = value.strip()
    return result


class UnitTestAllCommandTest(unittest.TestCase):
    def test_command_uses_primary_orchestrator_without_extra_subtask(self) -> None:
        text = COMMAND.read_text(encoding="utf-8")
        metadata = frontmatter(text)

        self.assertEqual(metadata["agent"], "unit-test-orchestrator")
        self.assertEqual(metadata["subtask"], "false")
        self.assertIn("unit-test-all/task-smoke/v1", text)
        self.assertIn("內建 Task", text)
        self.assertIn("不撰寫、不驗證、也不提交", text)
        self.assertNotIn("dispatch_unit_tests", text)

    def test_orchestrator_can_only_call_unit_test_subagent(self) -> None:
        text = ORCHESTRATOR.read_text(encoding="utf-8")

        self.assertRegex(
            text,
            r'(?ms)^  task:\n    "\*": deny\n    unit-test: allow$',
        )
        self.assertIn("unit-test-all/task-smoke/v1", text)
        self.assertIn("`subagent_type` 必須是 `unit-test`", text)
        self.assertIn("每個 Service 恰好呼叫一次", text)
        self.assertNotIn("dispatch_unit_tests: allow", text)

    def test_worker_can_only_edit_nested_worktree_test_paths(self) -> None:
        text = UNIT_TEST_AGENT.read_text(encoding="utf-8")

        self.assertRegex(
            text,
            r'(?ms)^  edit:\n    "\*": deny\n    "unit-test-worktrees/\*\*/src/test/\*\*": allow$',
        )
        self.assertRegex(text, r"(?m)^  validate_unit_tests: deny$")
        self.assertRegex(text, r"(?m)^  submit_unit_tests: deny$")
        self.assertIn("TASK_SMOKE_OK", text)

    def test_old_sdk_dispatch_files_are_gone(self) -> None:
        self.assertFalse((PROJECT_ROOT / ".opencode/plugins").exists())
        self.assertFalse((PROJECT_ROOT / ".opencode/lib").exists())

        tool_sources = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (PROJECT_ROOT / ".opencode/tools").glob("*.ts")
        )
        self.assertNotIn('from "../lib/', tool_sources)


if __name__ == "__main__":
    unittest.main()
