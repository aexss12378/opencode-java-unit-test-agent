import { tool } from "@opencode-ai/plugin"
import path from "node:path"

type BackendResult = { status?: string; [key: string]: unknown }

async function runBackend(
  projectRoot: string,
  sessionID: string,
  input: unknown,
  abort: AbortSignal,
): Promise<BackendResult> {
  const child = Bun.spawn(
    [
      "uv",
      "run",
      "--no-project",
      "python",
      path.join(import.meta.dir, "prepare_unit_test_workspaces.py"),
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
      message: wasCancelled ? "工作已取消。" : "準備工具沒有回傳有效 JSON",
      exit_code: exitCode,
      stderr: stderr.trim().slice(-4000),
    }
  }
}

const specificationSource = tool.schema
  .string()
  .trim()
  .min(1)
  .max(4000)
  .describe("可信規格檔案路徑，或以「使用者需求：」開頭的明確需求")

export default tool({
  description:
    "完整核對所有具體 Service 的分類，再為每個可執行 Service 建立獨立分支與專案內可見工作樹。只準備派工，不撰寫、驗證或發布測試。",
  args: {
    execution_mode: tool.schema.literal("unit-test-all/v2"),
    targets: tool.schema
      .array(
        tool.schema.object({
          target_class: tool.schema.string().trim().min(1),
          specification_sources: tool.schema.array(specificationSource).min(1).max(20),
        }),
      )
      .max(50),
    not_started: tool.schema
      .array(
        tool.schema.object({
          target_class: tool.schema.string().trim().min(1),
          reason: tool.schema.enum(["缺少可信規格證據", "可信規格彼此衝突"]),
        }),
      )
      .max(50),
  },
  async execute(args, context) {
    if (context.agent !== "unit-test-orchestrator") {
      return JSON.stringify({
        status: "blocked",
        message: `prepare_unit_test_workspaces 只允許 unit-test-orchestrator 使用，目前代理為 ${context.agent}`,
      })
    }
    return JSON.stringify(
      await runBackend(context.worktree, context.sessionID, args, context.abort),
    )
  },
})
