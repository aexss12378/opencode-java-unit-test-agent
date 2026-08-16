import { tool } from "@opencode-ai/plugin"
import path from "node:path"

async function runBackend(root: string, input: unknown, abort: AbortSignal) {
  const child = Bun.spawn(["uv", "run", "--script", path.join(import.meta.dir, "validate_javadocs.py"), "--repo", root], {
    cwd: root, env: { ...Bun.env, CI: "true", TERM: "dumb", PYTHONDONTWRITEBYTECODE: "1", UV_NO_PROGRESS: "1" },
    stdin: "pipe", stdout: "pipe", stderr: "pipe",
  })
  child.stdin.write(JSON.stringify(input)); child.stdin.end()
  let cancelled = abort.aborted
  const cancel = () => { cancelled = true; child.kill("SIGTERM") }
  if (cancelled) cancel(); else abort.addEventListener("abort", cancel, { once: true })
  let stdout = "", stderr = "", exitCode = 0
  try { ;[stdout, stderr, exitCode] = await Promise.all([new Response(child.stdout).text(), new Response(child.stderr).text(), child.exited]) }
  finally { abort.removeEventListener("abort", cancel) }
  try { return JSON.parse(stdout.trim()) }
  catch { return { status: cancelled ? "cancelled" : "tool-error", message: cancelled ? "工作已取消。" : "驗證工具沒有回傳有效 JSON", exit_code: exitCode, stderr: stderr.trim().slice(-4000) } }
}

export default tool({
  description: "排除未完成的逐檔結果，驗證差異只有 Javadoc，然後執行 Maven 編譯與專案既有的 Javadoc 檢查。",
  args: {
    worktree: tool.schema.string().regex(/^javadoc-worktrees\/[0-9a-f-]{36}$/),
    file_results: tool.schema.array(tool.schema.object({
      path: tool.schema.string().min(1),
      status: tool.schema.enum(["completed", "failed"]),
      message: tool.schema.string().max(4000).optional(),
      blocked_declarations: tool.schema.array(tool.schema.object({
        line: tool.schema.number().int().positive(),
        name: tool.schema.string().min(1),
        reason: tool.schema.string().min(1).max(4000),
      })).optional(),
    })),
  },
  async execute(args, context) { return JSON.stringify(await runBackend(context.worktree, args, context.abort)) },
})
