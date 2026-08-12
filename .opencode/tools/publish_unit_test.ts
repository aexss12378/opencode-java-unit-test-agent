import { tool } from "@opencode-ai/plugin"
import path from "node:path"

type BackendResult = { status?: string; [key: string]: unknown }

async function runBackend(
  projectRoot: string,
  input: unknown,
  abort: AbortSignal,
): Promise<BackendResult> {
  const child = Bun.spawn(
    [
      "uv",
      "run",
      "--no-project",
      "python",
      path.join(import.meta.dir, "publish_unit_test.py"),
      "--repo",
      projectRoot,
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
  child.stdin.write(JSON.stringify(input))
  child.stdin.end()
  let wasCancelled = abort.aborted
  const cancel = () => {
    wasCancelled = true
    child.kill("SIGTERM")
  }
  if (wasCancelled) cancel()
  else abort.addEventListener("abort", cancel, { once: true })
  let stdout: string
  let stderr: string
  let exitCode: number
  try {
    ;[stdout, stderr, exitCode] = await Promise.all([
      new Response(child.stdout).text(),
      new Response(child.stderr).text(),
      child.exited,
    ])
  } finally {
    abort.removeEventListener("abort", cancel)
  }
  try {
    return JSON.parse(stdout.trim()) as BackendResult
  } catch {
    return {
      status: wasCancelled ? "cancelled" : "tool-error",
      message: wasCancelled ? "工作已取消。" : "發布工具沒有回傳有效 JSON",
      exit_code: exitCode,
      stderr: stderr.trim().slice(-4000),
    }
  }
}

export default tool({
  description:
    "將 validate_unit_test 通過的候選測試建立分支、提交、推送並建立 Draft PR；不會轉為 Ready、合併或清理 worktree。",
  args: {
    target_class: tool.schema
      .string()
      .trim()
      .regex(/^[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)+$/),
    worktree: tool.schema
      .string()
      .trim()
      .regex(/^unit-test-worktrees\/[a-z0-9][a-z0-9-]*$/),
  },
  async execute(args, context) {
    if (context.agent !== "unit-test") {
      return JSON.stringify({
        status: "blocked",
        message: `publish_unit_test 只允許 unit-test 子代理使用，目前代理為 ${context.agent}`,
      })
    }
    return JSON.stringify(
      await runBackend(context.worktree, args, context.abort),
    )
  },
})
