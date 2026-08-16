import { tool } from "@opencode-ai/plugin"
import path from "node:path"

async function runBackend(root: string, input: unknown, abort: AbortSignal) {
  const child = Bun.spawn(["uv", "run", "--script", path.join(import.meta.dir, "apply_javadocs.py"), "--repo", root], {
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
  catch { return { status: cancelled ? "cancelled" : "tool-error", message: cancelled ? "工作已取消。" : "寫入工具沒有回傳有效 JSON", exit_code: exitCode, stderr: stderr.trim().slice(-4000) } }
}

export default tool({
  description: "對本次執行中的單一 Java 檔案，依目前行號與宣告名稱原子新增或整段取代一批 Javadoc。任一項不合法時整批不寫入。",
  args: {
    worktree: tool.schema.string().regex(/^javadoc-worktrees\/[0-9a-f-]{36}$/),
    path: tool.schema.string().min(1),
    changes: tool.schema.array(tool.schema.object({
      line: tool.schema.number().int().positive(),
      name: tool.schema.string().min(1),
      javadoc: tool.schema.string().min(1).max(50000).describe("只填 Javadoc 內文，不含 /**、行首 * 或 */。"),
    })).min(1).max(100),
  },
  async execute(args, context) { return JSON.stringify(await runBackend(context.worktree, args, context.abort)) },
})
