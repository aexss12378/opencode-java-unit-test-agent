import { tool } from "@opencode-ai/plugin"
import path from "node:path"

export default tool({
  description: "提交已核准的 JUnit 候選測試。工具會在隔離副本強制執行 Maven 基準、編譯、測試、JaCoCo 與限定範圍 PIT；全部完成後才發布新的 src/test/** 檔案。執行測試與 Maven 外掛可能執行專案程式碼或下載相依套件。",
  args: {
    approved_intent_ids: tool.schema
      .array(tool.schema.string())
      .min(1)
      .max(50)
      .describe("使用者已明確核准的測試意圖編號，例如 UT-001"),
    target_classes: tool.schema
      .array(tool.schema.string())
      .min(1)
      .max(10)
      .describe("PIT 限定的完整正式類別名稱，不接受萬用字元"),
    files: tool.schema
      .array(
        tool.schema.object({
          path: tool.schema
            .string()
            .describe("根目錄或模組下 src/test/java/** 的新 Java 測試檔相對路徑"),
          content: tool.schema.string().describe("完整 Java 測試原始碼"),
        }),
      )
      .min(1)
      .max(10),
  },
  async execute(args, context) {
    if (context.agent !== "unit-test") {
      return JSON.stringify({
        status: "blocked",
        message: `submit_unit_tests 只允許 unit-test 代理使用，目前代理為 ${context.agent}`,
      })
    }

    const projectRoot = context.worktree || context.directory
    const script = path.join(
      projectRoot,
      ".opencode/tools/submit_unit_tests.py",
    )
    const process = Bun.spawn(
      [
        "uv",
        "run",
        "--no-project",
        "python",
        script,
        "--repo",
        projectRoot,
        "--session-id",
        context.sessionID,
        "--publish",
      ],
      {
        cwd: projectRoot,
        env: {
          ...Bun.env,
          CI: "true",
          TERM: "dumb",
        },
        stdin: "pipe",
        stdout: "pipe",
        stderr: "pipe",
      },
    )

    process.stdin.write(JSON.stringify(args))
    process.stdin.end()

    const [stdout, stderr, exitCode] = await Promise.all([
      new Response(process.stdout).text(),
      new Response(process.stderr).text(),
      process.exited,
    ])
    if (stdout.trim()) return stdout.trim()

    return JSON.stringify({
      status: "tool-error",
      message: "驗證程式沒有回傳 JSON 結果",
      exit_code: exitCode,
      stderr: stderr.trim().slice(-4000),
    })
  },
})
