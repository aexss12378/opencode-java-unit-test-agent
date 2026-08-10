import { tool } from "@opencode-ai/plugin"
import path from "node:path"

type BackendResult = {
  status?: string
  [key: string]: unknown
}

async function runUnitTestBackend(
  projectRoot: string,
  sessionID: string,
  abort: AbortSignal,
): Promise<BackendResult> {
  const script = path.join(import.meta.dir, "submit_unit_tests.py")
  const process = Bun.spawn(
    [
      "uv",
      "run",
      "--no-project",
      "python",
      script,
      "validate",
      "--repo",
      projectRoot,
      "--session-id",
      sessionID,
    ],
    {
      cwd: projectRoot,
      env: {
        ...Bun.env,
        CI: "true",
        TERM: "dumb",
        PYTHONDONTWRITEBYTECODE: "1",
        UV_NO_PROGRESS: "1",
      },
      stdin: "pipe",
      stdout: "pipe",
      stderr: "pipe",
    },
  )

  process.stdin.write("{}")
  process.stdin.end()

  let cancelled = abort.aborted
  const cancel = () => {
    cancelled = true
    process.kill("SIGTERM")
  }
  if (cancelled) cancel()
  else abort.addEventListener("abort", cancel, { once: true })

  let stdout: string
  let stderr: string
  let exitCode: number
  try {
    ;[stdout, stderr, exitCode] = await Promise.all([
      new Response(process.stdout).text(),
      new Response(process.stderr).text(),
      process.exited,
    ])
  } finally {
    abort.removeEventListener("abort", cancel)
  }

  try {
    return JSON.parse(stdout.trim()) as BackendResult
  } catch {
    return {
      status: cancelled ? "cancelled" : "tool-error",
      message: cancelled
        ? "單元測試工作已取消；後端未完成可驗證的結果。"
        : "單元測試後端沒有回傳有效 JSON",
      exit_code: exitCode,
      stderr: stderr.trim().slice(-4000),
    }
  }
}

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
      await runUnitTestBackend(context.worktree, context.sessionID, context.abort),
    )
  },
})
