import { tool, type Plugin } from "@opencode-ai/plugin"
import { dispatchUnitTests } from "../lib/unit_test_dispatch"

const sourceText = tool.schema
  .string()
  .trim()
  .min(1)
  .max(4000)
  .describe(
    "實際存在的 README、docs、src/main/resources、目標類別公開 Javadoc 檔案位置，或以「使用者需求：」開頭的目前對話明確需求",
  )

export const UnitTestDispatchPlugin: Plugin = async ({ client }) => ({
  tool: {
    dispatch_unit_tests: tool({
      description:
        "核對完整 Service 分類與可信規格來源，再為每個可派工 Java Service 建立獨立本機分支、Git worktree 與 unit-test 子代理。完成後彙整 Maven、JaCoCo 與保留的 worktree，不提交、不推送也不建立 PR。",
      args: {
        execution_mode: tool.schema
          .enum(["unit-test-all/v1", "confirmed-targets"])
          .describe("批次指令固定使用 unit-test-all/v1；人工確認的指定範圍使用 confirmed-targets"),
        targets: tool.schema
          .array(
            tool.schema.object({
              target_class: tool.schema
                .string()
                .trim()
                .describe("已確認且以 Service 結尾的完整 Java 類別名稱"),
              specification_sources: tool.schema
                .array(sourceText)
                .min(1)
                .max(20),
            }),
          )
          .max(50)
          .describe("可派工的 Service；unit-test-all/v1 允許全部因規格原因未開始而傳入空陣列"),
        not_started: tool.schema
          .array(
            tool.schema.object({
              target_class: tool.schema
                .string()
                .trim()
                .describe("固定範圍內因規格原因不派工的完整 Service 類別名稱"),
              reason: tool.schema.enum(["缺少可信規格證據", "可信規格彼此衝突"]),
            }),
          )
          .max(50)
          .describe("unit-test-all/v1 必須列出每個不派工 Service；沒有時傳入空陣列"),
        max_concurrency: tool.schema
          .number()
          .int()
          .min(1)
          .max(8)
          .describe("同時執行的 Maven／子代理數量"),
      },
      async execute(args, context) {
        if (context.agent !== "unit-test-orchestrator") {
          return JSON.stringify({
            status: "blocked",
            message: `dispatch_unit_tests 只允許 unit-test-orchestrator 使用，目前代理為 ${context.agent}`,
          })
        }
        return JSON.stringify(
          await dispatchUnitTests(client, args, {
            sessionID: context.sessionID,
            worktree: context.worktree,
            abort: context.abort,
          }),
        )
      },
    }),
  },
})
