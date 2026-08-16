import { tool } from "@opencode-ai/plugin"
import path from "node:path"

async function runBackend(root: string, input: unknown, abort: AbortSignal) {
  const child = Bun.spawn(["uv", "run", "--script", path.join(import.meta.dir, "publish_javadocs.py"), "--repo", root], {
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
  catch { return { status: cancelled ? "cancelled" : "tool-error", message: cancelled ? "工作已取消。" : "發布工具沒有回傳有效 JSON", exit_code: exitCode, stderr: stderr.trim().slice(-4000) } }
}

export default tool({
  description: "將驗證通過的 Javadoc-only 變更建立單一提交、推送，並透過可替換介面建立 Draft PR。目前實作 GitHub；不會轉為 Ready 或合併。",
  args: {
    worktree: tool.schema.string().regex(/^javadoc-worktrees\/[0-9a-f-]{36}$/),
    publisher: tool.schema.enum(["github"]).default("github"),
  },
  async execute(args, context) { return JSON.stringify(await runBackend(context.worktree, args, context.abort)) },
})
