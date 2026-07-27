from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[2]
BACKEND = REPOSITORY / ".opencode" / "tools" / "javadoc_edit_backend.py"
SOURCE_PATH = "src/main/java/example/Sample.java"


def invoke(
    repository: Path,
    path: str,
    additions: list[tuple[int, str]],
) -> dict[str, object]:
    result = subprocess.run(
        [
            "uv",
            "run",
            "--script",
            str(BACKEND),
            "--repo",
            str(repository),
        ],
        cwd=repository,
        input=json.dumps(
            {
                "path": path,
                "additions": [
                    {"target_line": line, "javadoc": javadoc}
                    for line, javadoc in additions
                ],
            },
            ensure_ascii=False,
        ),
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
        env={
            **os.environ,
            "PYTHONDONTWRITEBYTECODE": "1",
            "UV_NO_PROGRESS": "1",
        },
    )
    if result.returncode != 0:
        raise AssertionError(
            f"後端程序失敗：{result.returncode}\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise AssertionError(
            f"後端輸出不是 JSON：\n{result.stdout}\n{result.stderr}"
        ) from error


class JavadocEditBackendTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.repository = Path(self.temporary.name)
        self.source = self.repository / SOURCE_PATH
        self.source.parent.mkdir(parents=True)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write(self, content: str) -> None:
        with self.source.open("w", encoding="utf-8", newline="") as stream:
            stream.write(content)

    def read(self) -> str:
        with self.source.open("r", encoding="utf-8", newline="") as stream:
            return stream.read()

    def assert_blocked_without_write(
        self,
        before: str,
        result: dict[str, object],
    ) -> None:
        self.assertEqual("blocked", result["status"])
        self.assertIs(False, result["written"])
        self.assertEqual(before, self.read())

    def test_adds_batch_from_bottom_without_line_shift(self) -> None:
        before = (
            "package example;\n"
            "\n"
            "public class Sample {\n"
            "\n"
            "    private final int value;\n"
            "\n"
            "    public Sample(int value) {\n"
            "        this.value = value;\n"
            "    }\n"
            "\n"
            "    @Deprecated\n"
            "    public int value() {\n"
            "        return value;\n"
            "    }\n"
            "}\n"
        )
        self.write(before)

        result = invoke(
            self.repository,
            SOURCE_PATH,
            [
                (3, "保存一個整數值。"),
                (5, "目前保存的值。"),
                (7, "建立範例物件。\n\n@param value 初始值"),
                (11, "取得目前的值。\n\n@return 目前的值"),
            ],
        )

        self.assertEqual("published", result["status"])
        self.assertEqual(4, result["added"])
        self.assertEqual(
            (
                "package example;\n"
                "\n"
                "/**\n"
                " * 保存一個整數值。\n"
                " */\n"
                "public class Sample {\n"
                "\n"
                "    /**\n"
                "     * 目前保存的值。\n"
                "     */\n"
                "    private final int value;\n"
                "\n"
                "    /**\n"
                "     * 建立範例物件。\n"
                "     *\n"
                "     * @param value 初始值\n"
                "     */\n"
                "    public Sample(int value) {\n"
                "        this.value = value;\n"
                "    }\n"
                "\n"
                "    /**\n"
                "     * 取得目前的值。\n"
                "     *\n"
                "     * @return 目前的值\n"
                "     */\n"
                "    @Deprecated\n"
                "    public int value() {\n"
                "        return value;\n"
                "    }\n"
                "}\n"
            ),
            self.read(),
        )

    def test_adds_javadoc_before_annotation(self) -> None:
        before = (
            "package example;\n"
            "\n"
            "@FunctionalInterface\n"
            "public interface Sample {\n"
            "    int value();\n"
            "}\n"
        )
        self.write(before)

        result = invoke(self.repository, SOURCE_PATH, [(3, "提供範例值。")])

        self.assertEqual("published", result["status"])
        self.assertIn(
            "/**\n * 提供範例值。\n */\n@FunctionalInterface",
            self.read(),
        )

    def test_accepts_java_8_and_java_21_syntax_without_pom(self) -> None:
        java_21 = (
            "package example;\n"
            "\n"
            "public class Sample {\n"
            "    public String render(Object value) {\n"
            "        return switch (value) {\n"
            "            case String text -> text;\n"
            '            default -> "";\n'
            "        };\n"
            "    }\n"
            "}\n"
        )
        self.write(java_21)
        result = invoke(self.repository, SOURCE_PATH, [(3, "呈現指定值。")])
        self.assertEqual("published", result["status"])

        java_8 = (
            "package example;\n"
            "\n"
            "public class Sample {\n"
            "    public void run() {\n"
            "        int _ = 1;\n"
            "    }\n"
            "}\n"
        )
        self.write(java_8)
        result = invoke(self.repository, SOURCE_PATH, [(3, "執行範例。")])
        self.assertEqual("published", result["status"])

    def test_rejects_existing_javadoc(self) -> None:
        before = (
            "package example;\n"
            "\n"
            "/** Existing documentation. */\n"
            "public class Sample {}\n"
        )
        self.write(before)

        result = invoke(self.repository, SOURCE_PATH, [(4, "新的說明。")])

        self.assert_blocked_without_write(before, result)
        self.assertIs(True, result["retryable"])
        self.assertIn("已經有 Javadoc", str(result["message"]))

    def test_rejects_non_declaration_line(self) -> None:
        before = (
            "package example;\n"
            "\n"
            "public class Sample {\n"
            "    public int value() {\n"
            "        int local = 1;\n"
            "        return local;\n"
            "    }\n"
            "}\n"
        )
        self.write(before)

        result = invoke(self.repository, SOURCE_PATH, [(5, "區域變數不是文件目標。")])

        self.assert_blocked_without_write(before, result)
        self.assertIs(True, result["retryable"])
        self.assertIn("不是唯一且可新增", str(result["message"]))

    def test_rejects_declaration_text_inside_text_block(self) -> None:
        before = (
            "package example;\n"
            "\n"
            "public class Sample {\n"
            '    String example = """\n'
            "        public class Fake {}\n"
            '        """;\n'
            "}\n"
        )
        self.write(before)

        result = invoke(self.repository, SOURCE_PATH, [(5, "不得加入字串。")])

        self.assert_blocked_without_write(before, result)

    def test_rejects_unsafe_javadoc_content(self) -> None:
        before = "package example;\n\npublic class Sample {}\n"
        self.write(before)

        result = invoke(
            self.repository,
            SOURCE_PATH,
            [(3, "看似說明。 */ public class Injected {} /*")],
        )
        self.assert_blocked_without_write(before, result)
        self.assertIs(True, result["retryable"])

        result = invoke(
            self.repository,
            SOURCE_PATH,
            [(3, r"不得使用 \u002a\u002f 結束註解。")],
        )
        self.assert_blocked_without_write(before, result)

    def test_rejects_entire_batch_when_one_target_is_invalid(self) -> None:
        before = (
            "package example;\n"
            "\n"
            "public class Sample {\n"
            "    public int value() {\n"
            "        return 1;\n"
            "    }\n"
            "}\n"
        )
        self.write(before)

        result = invoke(
            self.repository,
            SOURCE_PATH,
            [(3, "類別說明。"), (5, "錯誤位置。")],
        )

        self.assert_blocked_without_write(before, result)

    def test_rejects_path_outside_main_java(self) -> None:
        before = "package example;\n\npublic class Sample {}\n"
        self.write(before)
        test_source = self.repository / "src/test/java/example/SampleTest.java"
        test_source.parent.mkdir(parents=True)
        test_source.write_text("public class SampleTest {}\n", encoding="utf-8")

        result = invoke(
            self.repository,
            "src/test/java/example/SampleTest.java",
            [(1, "測試說明。")],
        )

        self.assert_blocked_without_write(before, result)
        self.assertIs(False, result["retryable"])

    @unittest.skipUnless(hasattr(os, "symlink"), "平台不支援符號連結")
    def test_rejects_symbolic_link(self) -> None:
        outside = self.repository / "Outside.java"
        outside.write_text("public class Outside {}\n", encoding="utf-8")
        self.source.unlink(missing_ok=True)
        self.source.symlink_to(outside)

        result = invoke(self.repository, SOURCE_PATH, [(1, "外部檔案。")])

        self.assertEqual("blocked", result["status"])
        self.assertIs(False, result["written"])
        self.assertEqual(
            "public class Outside {}\n",
            outside.read_text(encoding="utf-8"),
        )

    def test_preserves_crlf(self) -> None:
        before = "package example;\r\n\r\npublic class Sample {}\r\n"
        self.write(before)

        result = invoke(self.repository, SOURCE_PATH, [(3, "類別說明。")])

        self.assertEqual("published", result["status"])
        after = self.source.read_bytes()
        self.assertNotIn(b"\n", after.replace(b"\r\n", b""))


if __name__ == "__main__":
    unittest.main()
