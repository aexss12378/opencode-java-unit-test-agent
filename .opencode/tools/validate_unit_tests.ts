import { tool } from "@opencode-ai/plugin"
import { runUnitTestBackend } from "../lib/unit_test_backend"

export default tool({
  description:
    "驗證目前派工 worktree 內的唯一 Service 測試檔。工具會清除該 worktree 的舊 target、執行指定測試類別的 Maven test，解析 Surefire 與 JaCoCo XML，並確認只有派工測試檔有 Git 變更；不提交也不推送。",
  args: {},
  async execute(_args, context) {
    if (context.agent !== "unit-test") {
      return JSON.stringify({
        status: "blocked",
        message: `validate_unit_tests 只允許 unit-test 工作代理使用，目前代理為 ${context.agent}`,
      })
    }
    return JSON.stringify(
      await runUnitTestBackend(
        "validate",
        context.worktree,
        context.sessionID,
        {},
        context.abort,
      ),
    )
  },
})
