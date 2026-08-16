import { tool } from "@opencode-ai/plugin"
import path from "node:path"

async function runBackend(projectRoot: string, input: unknown, abort: AbortSignal) {
  const child = Bun.spawn(["uv", "run", "--script", path.join(import.meta.dir, "apply_javadocs.py"), "--repo", projectRoot], {
    cwd: projectRoot,
    env: { ...Bun.env, CI: "true", TERM: "dumb", PYTHONDONTWRITEBYTECODE: "1", UV_NO_PROGRESS: "1" },
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
  catch { return { status: cancelled ? "cancelled" : "tool-error", message: cancelled ? "工作已取消。" : "寫入工具沒有回傳有效 JSON", exit_code: exitCode, stderr: stderr.trim().slice(-4000) } }
}

const review = tool.schema.object({
  key: tool.schema.string().min(1),
  decision: tool.schema.enum(["write", "skip", "blocked"]),
  javadoc: tool.schema.string().max(50000).optional().describe("只填 Javadoc 內文，不含 /**、行首 * 或 */。"),
  reason: tool.schema.string().max(4000).optional(),
})

export default tool({
  description: "對本次執行中的單一 Java 檔案原子套用一批 Javadoc 審查決策。每個宣告只能審查一次；批次中任一項不合法時整批不寫入。",
  args: {
    worktree: tool.schema.string().regex(/^javadoc-worktrees\/[0-9a-f-]{36}$/),
    path: tool.schema.string().min(1),
    reviews: tool.schema.array(review).min(1).max(100),
  },
  async execute(args, context) { return JSON.stringify(await runBackend(context.worktree, args, context.abort)) },
})
