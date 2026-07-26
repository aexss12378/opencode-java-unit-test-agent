from __future__ import annotations

import base64
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[2]
BACKEND = REPOSITORY / ".opencode" / "tools" / "JavadocEditBackend.java"
SOURCE_PATH = "src/main/java/example/Sample.java"


def encode(value: str) -> str:
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def invoke(
    repository: Path,
    path: str,
    additions: list[tuple[int, str]],
) -> dict[str, object]:
    payload = [
        encode(path),
        str(len(additions)),
        *(f"{line}\t{encode(javadoc)}" for line, javadoc in additions),
        "",
    ]
    result = subprocess.run(
        [
            "java",
            "--source",
            "17",
            str(BACKEND),
            "--repo",
            str(repository),
        ],
        cwd=repository,
        input="\n".join(payload),
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
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
        self.write_pom("maven.compiler.release", "17")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write(self, content: str) -> None:
        with self.source.open("w", encoding="utf-8", newline="") as stream:
            stream.write(content)

    def write_pom(self, property_name: str | None, value: str | None) -> None:
        property_xml = (
            f"<{property_name}>{value}</{property_name}>"
            if property_name is not None and value is not None
            else ""
        )
        (self.repository / "pom.xml").write_text(
            (
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<project xmlns="http://maven.apache.org/POM/4.0.0">\n'
                "  <modelVersion>4.0.0</modelVersion>\n"
                "  <groupId>example</groupId>\n"
                "  <artifactId>sample</artifactId>\n"
                "  <version>1.0</version>\n"
                f"  <properties>{property_xml}</properties>\n"
                "</project>\n"
            ),
            encoding="utf-8",
        )

    def read(self) -> str:
        with self.source.open("r", encoding="utf-8", newline="") as stream:
            return stream.read()

    def test_adds_multiple_javadocs_to_one_file_without_other_changes(self) -> None:
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
        self.assertEqual(17, result["java_release"])
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

    def test_adds_javadoc_before_type_annotation(self) -> None:
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
        self.assertEqual(
            (
                "package example;\n"
                "\n"
                "/**\n"
                " * 提供範例值。\n"
                " */\n"
                "@FunctionalInterface\n"
                "public interface Sample {\n"
                "    int value();\n"
                "}\n"
            ),
            self.read(),
        )

    def test_uses_java_8_from_maven_compiler_source(self) -> None:
        self.write_pom("maven.compiler.source", "1.8")
        before = (
            "package example;\n"
            "\n"
            "public class Sample {\n"
            "    public void run() {\n"
            "        int _ = 1;\n"
            "    }\n"
            "}\n"
        )
        self.write(before)

        result = invoke(self.repository, SOURCE_PATH, [(3, "執行範例。")])

        self.assertEqual("published", result["status"])
        self.assertEqual(8, result["java_release"])
        self.assertIn("/**\n * 執行範例。\n */\npublic class", self.read())

    def test_uses_java_21_from_java_version(self) -> None:
        self.write_pom("java.version", "21")
        before = (
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
        self.write(before)

        result = invoke(self.repository, SOURCE_PATH, [(3, "呈現指定值。")])

        self.assertEqual("published", result["status"])
        self.assertEqual(21, result["java_release"])
        self.assertIn("/**\n * 呈現指定值。\n */\npublic class", self.read())

    def test_resolves_java_release_property_reference(self) -> None:
        (self.repository / "pom.xml").write_text(
            (
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<project xmlns="http://maven.apache.org/POM/4.0.0">\n'
                "  <modelVersion>4.0.0</modelVersion>\n"
                "  <properties>\n"
                "    <java.version>21</java.version>\n"
                "    <maven.compiler.release>${java.version}</maven.compiler.release>\n"
                "  </properties>\n"
                "</project>\n"
            ),
            encoding="utf-8",
        )
        self.write("package example;\n\npublic class Sample {}\n")

        result = invoke(self.repository, SOURCE_PATH, [(3, "範例類別。")])

        self.assertEqual("published", result["status"])
        self.assertEqual(21, result["java_release"])

    def test_rejects_missing_java_release(self) -> None:
        self.write_pom(None, None)
        before = "package example;\n\npublic class Sample {}\n"
        self.write(before)

        result = invoke(self.repository, SOURCE_PATH, [(3, "範例類別。")])

        self.assertEqual("blocked", result["status"])
        self.assertEqual("JAVA_RELEASE_NOT_FOUND", result["code"])
        self.assertIs(False, result["retryable"])
        self.assertIs(False, result["written"])
        self.assertEqual(before, self.read())

    def test_rejects_unsupported_java_release(self) -> None:
        self.write_pom("java.version", "11")
        before = "package example;\n\npublic class Sample {}\n"
        self.write(before)

        result = invoke(self.repository, SOURCE_PATH, [(3, "範例類別。")])

        self.assertEqual("blocked", result["status"])
        self.assertEqual("JAVA_RELEASE_NOT_SUPPORTED", result["code"])
        self.assertIs(False, result["retryable"])
        self.assertIs(False, result["written"])
        self.assertEqual(before, self.read())

    def test_rejects_declaration_that_already_has_javadoc(self) -> None:
        before = (
            "package example;\n"
            "\n"
            "/** Existing documentation. */\n"
            "public class Sample {}\n"
        )
        self.write(before)

        result = invoke(self.repository, SOURCE_PATH, [(4, "新的說明。")])

        self.assertEqual("blocked", result["status"])
        self.assertEqual("ALREADY_DOCUMENTED", result["code"])
        self.assertIs(True, result["retryable"])
        self.assertIs(False, result["written"])
        self.assertIn("已經有 Javadoc", str(result["message"]))
        self.assertIn("重新讀取", str(result["message"]))
        self.assertEqual(before, self.read())

    def test_rejects_path_outside_main_java(self) -> None:
        before = "package example;\n\npublic class Sample {}\n"
        self.write(before)
        test_source = self.repository / "src/test/java/example/SampleTest.java"
        test_source.parent.mkdir(parents=True)
        test_source.write_text(
            "package example;\n\npublic class SampleTest {}\n",
            encoding="utf-8",
        )

        result = invoke(
            self.repository,
            "src/test/java/example/SampleTest.java",
            [(3, "測試說明。")],
        )

        self.assertEqual("blocked", result["status"])
        self.assertEqual("PATH_NOT_ALLOWED", result["code"])
        self.assertIs(False, result["retryable"])
        self.assertIn("src/main/java", str(result["message"]))
        self.assertEqual(before, self.read())

    def test_rejects_comment_terminator_in_javadoc(self) -> None:
        before = "package example;\n\npublic class Sample {}\n"
        self.write(before)

        result = invoke(
            self.repository,
            SOURCE_PATH,
            [(3, "看似說明。 */ public class Injected {} /*")],
        )

        self.assertEqual("blocked", result["status"])
        self.assertEqual("UNSAFE_JAVADOC_CONTENT", result["code"])
        self.assertIs(True, result["retryable"])
        self.assertIs(False, result["written"])
        self.assertIn("提早結束", str(result["message"]))
        self.assertIn("只修正 Javadoc", str(result["message"]))
        self.assertEqual(before, self.read())

    def test_rejects_unicode_escape_in_javadoc(self) -> None:
        before = "package example;\n\npublic class Sample {}\n"
        self.write(before)

        result = invoke(
            self.repository,
            SOURCE_PATH,
            [(3, r"不得使用 \u002a\u002f 結束註解。")],
        )

        self.assertEqual("blocked", result["status"])
        self.assertEqual("UNSAFE_JAVADOC_CONTENT", result["code"])
        self.assertIs(True, result["retryable"])
        self.assertIn("提早結束", str(result["message"]))
        self.assertEqual(before, self.read())

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
            [(3, "類別說明。"), (5, "這裡是方法內容，不是宣告。")],
        )

        self.assertEqual("blocked", result["status"])
        self.assertEqual("TARGET_NOT_DECLARATION", result["code"])
        self.assertIs(True, result["retryable"])
        self.assertIs(False, result["written"])
        self.assertIn("不是可新增 Javadoc", str(result["message"]))
        self.assertIn("重新讀取", str(result["message"]))
        self.assertEqual(before, self.read())

    @unittest.skipUnless(hasattr(os, "symlink"), "平台不支援符號連結")
    def test_rejects_symbolic_link_target(self) -> None:
        outside = self.repository / "Outside.java"
        outside.write_text(
            "package example;\n\npublic class Outside {}\n",
            encoding="utf-8",
        )
        self.source.unlink(missing_ok=True)
        self.source.symlink_to(outside)
        before = outside.read_text(encoding="utf-8")

        result = invoke(self.repository, SOURCE_PATH, [(3, "外部檔案說明。")])

        self.assertEqual("blocked", result["status"])
        self.assertEqual("SYMLINK_NOT_ALLOWED", result["code"])
        self.assertIs(False, result["retryable"])
        self.assertIn("符號連結", str(result["message"]))
        self.assertEqual(before, outside.read_text(encoding="utf-8"))

    @unittest.skipUnless(hasattr(os, "symlink"), "平台不支援符號連結")
    def test_rejects_symbolic_link_directory_component(self) -> None:
        real_directory = self.repository / "src/main/java/real"
        real_directory.mkdir()
        real_source = real_directory / "Sample.java"
        real_source.write_text(
            "package real;\n\npublic class Sample {}\n",
            encoding="utf-8",
        )
        linked_directory = self.repository / "src/main/java/linked"
        linked_directory.symlink_to(real_directory, target_is_directory=True)
        before = real_source.read_text(encoding="utf-8")

        result = invoke(
            self.repository,
            "src/main/java/linked/Sample.java",
            [(3, "符號連結目錄中的檔案。")],
        )

        self.assertEqual("blocked", result["status"])
        self.assertEqual("SYMLINK_NOT_ALLOWED", result["code"])
        self.assertIs(False, result["retryable"])
        self.assertIn("不得包含符號連結", str(result["message"]))
        self.assertEqual(before, real_source.read_text(encoding="utf-8"))

    def test_preserves_crlf_line_endings(self) -> None:
        before = "package example;\r\n\r\npublic class Sample {}\r\n"
        self.write(before)

        result = invoke(self.repository, SOURCE_PATH, [(3, "類別說明。")])

        self.assertEqual("published", result["status"])
        after = self.source.read_bytes()
        self.assertNotIn(b"\n", after.replace(b"\r\n", b""))
        self.assertEqual(
            (
                "package example;\r\n"
                "\r\n"
                "/**\r\n"
                " * 類別說明。\r\n"
                " */\r\n"
                "public class Sample {}\r\n"
            ).encode(),
            after,
        )


if __name__ == "__main__":
    unittest.main()
