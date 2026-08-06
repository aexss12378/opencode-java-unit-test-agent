import { tool } from "@opencode-ai/plugin"
import { runUnitTestBackend } from "../lib/unit_test_backend"

const caseText = (description: string) =>
  tool.schema.string().trim().min(1).max(4000).describe(description)

export default tool({
  description:
    "發布目前派工 worktree 內已完成的唯一 Service 測試檔。工具會重新執行 Maven 與 JaCoCo 驗證、只提交派工測試檔、推送既定分支、建立 Draft PR，並核對遠端 SHA 與 PR 狀態；不建立 worktree、不接受檔案內容，也絕不合併。",
  args: {
    test_cases: tool.schema
      .array(
        tool.schema.object({
          id: tool.schema
            .string()
            .trim()
            .regex(/^UT-[0-9]{3,}$/)
            .describe("測試案例編號，例如 UT-001"),
          scenario: caseText("測試情境、輸入與前置條件"),
          expected: caseText("可觀察的預期結果"),
          evidence: caseText("專案檔案位置或工程師提供的需求"),
        }),
      )
      .min(1)
      .max(50),
  },
  async execute(args, context) {
    if (context.agent !== "unit-test") {
      return JSON.stringify({
        status: "blocked",
        message: `submit_unit_tests 只允許 unit-test 工作代理使用，目前代理為 ${context.agent}`,
      })
    }
    return JSON.stringify(
      await runUnitTestBackend(
        "submit",
        context.worktree,
        context.sessionID,
        args,
        context.abort,
      ),
    )
  },
})
