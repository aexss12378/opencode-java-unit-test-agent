import { tool, type Plugin } from "@opencode-ai/plugin"
import { dispatchUnitTests } from "../lib/unit_test_dispatch"

const sourceText = tool.schema
  .string()
  .trim()
  .min(1)
  .max(4000)
  .describe("可信規格檔案位置，或工程師在目前對話中明確提供的需求")

export const UnitTestDispatchPlugin: Plugin = async ({ client }) => ({
  tool: {
    dispatch_unit_tests: tool({
      description:
        "由主代理為每個已確認的 Java Service 建立獨立分支與 Git worktree，並建立掛在目前主工作階段下的 unit-test 子代理。每個子代理的工作目錄固定為自己的 worktree；完成後彙整 JaCoCo 行覆蓋率與已驗證 Draft PR。",
      args: {
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
          .min(1)
          .max(50),
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
