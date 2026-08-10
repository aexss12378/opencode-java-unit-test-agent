from __future__ import annotations

import re
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
COMMAND = PROJECT_ROOT / ".opencode/commands/unit-test-all.md"
ORCHESTRATOR = PROJECT_ROOT / ".opencode/agents/unit-test-orchestrator.md"
UNIT_TEST_AGENT = PROJECT_ROOT / ".opencode/agents/unit-test.md"
SKILL = PROJECT_ROOT / ".opencode/skills/springboot-java-unit-testing/SKILL.md"


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
        self.assertIn("unit-test-all/v1", text)
        self.assertIn("`max_concurrency` 設為 `2`", text)
        self.assertIn("`execution_mode: unit-test-all/v1`", text)
        self.assertIn("`not_started`", text)

    def test_orchestrator_can_dispatch_only_after_fixed_batch_contract(self) -> None:
        text = ORCHESTRATOR.read_text(encoding="utf-8")

        self.assertRegex(text, r"(?m)^  dispatch_unit_tests: allow$")
        self.assertIn("## `/unit-test-all` 預先授權批次模式", text)
        self.assertIn("unit-test-all/v1", text)
        self.assertIn("`max_concurrency: 2`", text)
        self.assertIn("缺少可信規格證據", text)
        self.assertIn("沒有公開 Javadoc 的正式原始碼不是規格來源", text)
        self.assertIn("檔案來源每一項只能傳入純檔案路徑", text)
        self.assertIn("工具會自行盤點 Service", text)

    def test_worker_can_validate_but_cannot_submit(self) -> None:
        text = UNIT_TEST_AGENT.read_text(encoding="utf-8")

        self.assertRegex(text, r"(?m)^  validate_unit_tests: allow$")
        self.assertRegex(text, r"(?m)^  submit_unit_tests: deny$")
        self.assertIn("不得提交、推送、建立 PR", text)

    def test_skill_recognizes_the_same_preapproved_mode(self) -> None:
        text = SKILL.read_text(encoding="utf-8")

        self.assertIn("unit-test-all/v1", text)
        self.assertIn("預先確認", text)
        self.assertIn("逐一列為未開始", text)


if __name__ == "__main__":
    unittest.main()
