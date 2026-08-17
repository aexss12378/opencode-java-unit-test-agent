from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

from validate_unit_test import Target, surefire_failures, validate


class ValidateUnitTestDiagnosticsTest(unittest.TestCase):
    def write_report(self, root: Path) -> None:
        report_directory = root / "target/surefire-reports"
        report_directory.mkdir(parents=True)
        (report_directory / "TEST-example.FooTest.xml").write_text(
            """<testsuite tests=\"2\" failures=\"1\" errors=\"1\">
              <testcase classname=\"example.FooTest\" name=\"assertion\">
                <failure type=\"org.opentest4j.AssertionFailedError\" message=\"expected: &lt;10&gt; but was: &lt;9&gt;\">org.opentest4j.AssertionFailedError: expected: &lt;10&gt; but was: &lt;9&gt;
                  at example.FooTest.assertion(FooTest.java:12)
                  at org.junit.jupiter.api.Assertions.assertEquals(Assertions.java:1)
                </failure>
                <system-out><![CDATA[input customerId=customer-42
]]></system-out>
                <system-err><![CDATA[inventoryGateway response=OUT_OF_STOCK
]]></system-err>
              </testcase>
              <testcase classname=\"example.FooTest\" name=\"runtime\">
                <error type=\"java.lang.IllegalStateException\" message=\"runtime problem\">java.lang.IllegalStateException: runtime problem
                  at example.Foo.doThing(Foo.java:87)
                  at example.FooTest.runtime(FooTest.java:20)
                </error>
                <system-err><![CDATA[paymentGateway response=timeout
]]></system-err>
              </testcase>
              <testcase classname="example.FooTest" name="noMessage">
                <error type="java.lang.IllegalStateException">java.lang.IllegalStateException
                  at example.FooTest.noMessage(FooTest.java:30)
                </error>
                <system-err><![CDATA[setup repository connection=closed
]]></system-err>
              </testcase>
            </testsuite>""",
            encoding="utf-8",
        )

    def test_surefire_summary_keeps_only_agent_relevant_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.write_report(root)

            result = surefire_failures(root, "example.FooTest", "example.Foo")

            self.assertNotIn("reports", result)
            self.assertEqual(len(result["failures"]), 3)
            self.assertEqual(result["failures"][0]["test_class"], "example.FooTest")
            self.assertEqual(result["failures"][0]["kind"], "assertion-failure")
            self.assertEqual(result["failures"][0]["test_source"], "example.FooTest.assertion(FooTest.java:12)")
            self.assertEqual(result["failures"][0]["system_out"], "input customerId=customer-42\n")
            self.assertEqual(result["failures"][0]["system_err"], "inventoryGateway response=OUT_OF_STOCK\n")
            self.assertNotIn("exception", result["failures"][0])
            self.assertEqual(result["failures"][1]["kind"], "runtime-error")
            self.assertEqual(result["failures"][1]["test_source"], "example.FooTest.runtime(FooTest.java:20)")
            self.assertEqual(result["failures"][1]["target_source"], "example.Foo.doThing(Foo.java:87)")
            self.assertEqual(result["failures"][1]["system_err"], "paymentGateway response=timeout\n")
            self.assertIsNone(result["failures"][2]["message"])
            self.assertEqual(result["failures"][2]["system_err"], "setup repository connection=closed\n")
            self.assertNotIn("org.junit.jupiter.api.Assertions", str(result))

    def test_validate_uses_surefire_failures_for_test_failures(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            test_file = root / "src/test/java/example/FooTest.java"
            test_file.parent.mkdir(parents=True)
            test_file.write_text("// UT-001\n", encoding="utf-8")
            self.write_report(root)
            target = Target(root, "unit-test-worktrees/foo", "example.Foo", "example.FooTest", "src/test/java/example/FooTest.java")

            def failed_maven(*_args: object, **_kwargs: object) -> dict[str, object]:
                self.write_report(root)
                return {"command": "mvn test", "exit_code": 1, "timed_out": False, "maven_errors": "[ERROR] test failed"}

            with patch(
                "validate_unit_test.run_maven",
                side_effect=failed_maven,
            ):
                result = validate(
                    target,
                    {"test_cases": [{"id": "UT-001", "scenario": "s", "expected": "e", "evidence": "目前實作：x"}]},
                )

            self.assertEqual(result["status"], "test-failed")
            self.assertEqual(result["diagnostic_field"], "surefire_failures")
            self.assertNotIn("maven_errors", result)
            self.assertNotIn("validation", result)

    def test_validate_uses_maven_failure_without_surefire_report(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            test_file = root / "src/test/java/example/FooTest.java"
            test_file.parent.mkdir(parents=True)
            test_file.write_text("// UT-001\n", encoding="utf-8")
            target = Target(root, "unit-test-worktrees/foo", "example.Foo", "example.FooTest", "src/test/java/example/FooTest.java")

            with patch(
                "validate_unit_test.run_maven",
                return_value={"command": "mvn test", "exit_code": 1, "timed_out": False, "maven_errors": "[ERROR] cannot find symbol"},
            ):
                result = validate(
                    target,
                    {"test_cases": [{"id": "UT-001", "scenario": "s", "expected": "e", "evidence": "目前實作：x"}]},
                )

            self.assertEqual(result["status"], "maven-failed")
            self.assertEqual(result["diagnostic_field"], "maven_errors")

    def test_validate_prioritizes_timeout_over_other_failure_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            test_file = root / "src/test/java/example/FooTest.java"
            test_file.parent.mkdir(parents=True)
            test_file.write_text("// UT-001\n", encoding="utf-8")
            self.write_report(root)
            target = Target(root, "unit-test-worktrees/foo", "example.Foo", "example.FooTest", "src/test/java/example/FooTest.java")

            with patch(
                "validate_unit_test.run_maven",
                return_value={
                    "command": "mvn test",
                    "exit_code": 124,
                    "timed_out": True,
                    "maven_errors": "[ERROR] process still running",
                },
            ):
                result = validate(
                    target,
                    {"test_cases": [{"id": "UT-001", "scenario": "s", "expected": "e", "evidence": "目前實作：x"}]},
                )

            self.assertEqual(result["status"], "validation-timeout")
            self.assertEqual(result["validation"]["timed_out"], True)
            self.assertNotIn("diagnostic_field", result)
            self.assertNotIn("surefire_failures", result)
            self.assertNotIn("maven_errors", result)


if __name__ == "__main__":
    unittest.main()
