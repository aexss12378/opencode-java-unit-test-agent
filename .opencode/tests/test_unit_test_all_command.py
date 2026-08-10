from __future__ import annotations

import re
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
COMMAND = PROJECT_ROOT / ".opencode/commands/unit-test-all.md"
ORCHESTRATOR = PROJECT_ROOT / ".opencode/agents/unit-test-orchestrator.md"
UNIT_TEST_AGENT = PROJECT_ROOT / ".opencode/agents/unit-test.md"
PREPARE_TOOL = PROJECT_ROOT / ".opencode/tools/prepare_unit_test_workspaces.ts"
PREPARE_BACKEND = PROJECT_ROOT / ".opencode/tools/prepare_unit_test_workspaces.py"


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
        self.assertIn("unit-test-all/v2", text)
        self.assertIn("一次觸發所有 Service 的子代理", text)
        self.assertIn("提交、推送並建立 Draft PR", text)
        self.assertIn("本次不執行清理", text)

    def test_orchestrator_prepares_then_only_calls_unit_test_subagent(self) -> None:
        text = ORCHESTRATOR.read_text(encoding="utf-8")

        self.assertRegex(
            text,
            r'(?ms)^  task:\n    "\*": deny\n    unit-test: allow$',
        )
        self.assertRegex(text, r"(?m)^  prepare_unit_test_workspaces: allow$")
        self.assertRegex(text, r"(?m)^  validate_unit_test: deny$")
        self.assertRegex(text, r"(?m)^  publish_unit_test: deny$")
        self.assertIn("unit-test-all/v2", text)
        self.assertIn("`subagent_type` 必須是 `unit-test`", text)
        self.assertIn("每個項目恰好呼叫一次", text)
        self.assertIn("同時送出全部 Task 呼叫", text)
        self.assertIn("不得分批", text)
        self.assertIn("每個 Service 都必須正好出現在 `targets` 一次", text)
        self.assertNotIn("完整 `not_started`", text)
        self.assertNotIn("max_concurrency", text)

    def test_all_service_dispatch_has_no_concurrency_cap(self) -> None:
        sources = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (COMMAND, ORCHESTRATOR, PREPARE_TOOL, PREPARE_BACKEND)
        )

        self.assertNotIn("max_concurrency", sources)
        self.assertNotIn("每批", sources)
        self.assertIn("一次觸發所有 Service", sources)

    def test_prepare_schema_only_accepts_optional_specification_paths(self) -> None:
        text = PREPARE_TOOL.read_text(encoding="utf-8")

        self.assertIn(".regex(/^(?:README", text)
        self.assertIn(".optional()", text)
        self.assertNotIn("not_started", text)

    def test_worker_can_edit_nested_tests_validate_and_publish(self) -> None:
        text = UNIT_TEST_AGENT.read_text(encoding="utf-8")

        self.assertRegex(
            text,
            r'(?ms)^  edit:\n    "\*": deny\n    "unit-test-worktrees/\*\*/src/test/\*\*": allow$',
        )
        self.assertRegex(text, r"(?m)^  prepare_unit_test_workspaces: deny$")
        self.assertRegex(text, r"(?m)^  validate_unit_test: allow$")
        self.assertRegex(text, r"(?m)^  publish_unit_test: allow$")
        self.assertIn("最新 `validation_id`", text)
        self.assertIn("不得宣稱 PR 已轉為 Ready、已合併", text)

    def test_only_three_unit_test_custom_tools_are_exposed(self) -> None:
        self.assertFalse((PROJECT_ROOT / ".opencode/plugins").exists())
        self.assertFalse((PROJECT_ROOT / ".opencode/lib").exists())

        names = {
            path.stem
            for path in (PROJECT_ROOT / ".opencode/tools").glob("*.ts")
            if "unit_test" in path.stem
        }
        self.assertEqual(
            names,
            {
                "prepare_unit_test_workspaces",
                "validate_unit_test",
                "publish_unit_test",
            },
        )
        self.assertFalse(
            (PROJECT_ROOT / ".opencode/tools/submit_unit_tests.py").exists()
        )


if __name__ == "__main__":
    unittest.main()
