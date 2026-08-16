import { tool } from "@opencode-ai/plugin"
import path from "node:path"

async function runBackend(projectRoot: string, input: unknown, abort: AbortSignal) {
  const child = Bun.spawn(
    ["uv", "run", "--script", path.join(import.meta.dir, "prepare_javadoc_workspace.py"), "--repo", projectRoot],
    {
      cwd: projectRoot,
      env: { ...Bun.env, CI: "true", TERM: "dumb", PYTHONDONTWRITEBYTECODE: "1", UV_NO_PROGRESS: "1" },
      stdin: "pipe",
      stdout: "pipe",
      stderr: "pipe",
    },
  )
  child.stdin.write(JSON.stringify(input))
  child.stdin.end()
  let cancelled = abort.aborted
  const cancel = () => { cancelled = true; child.kill("SIGTERM") }
  if (cancelled) cancel()
  else abort.addEventListener("abort", cancel, { once: true })
  let stdout = ""
  let stderr = ""
  let exitCode = 0
  try {
    ;[stdout, stderr, exitCode] = await Promise.all([
      new Response(child.stdout).text(),
      new Response(child.stderr).text(),
      child.exited,
    ])
  } finally {
    abort.removeEventListener("abort", cancel)
  }
  try { return JSON.parse(stdout.trim()) }
  catch { return { status: cancelled ? "cancelled" : "tool-error", message: cancelled ? "工作已取消。" : "準備工具沒有回傳有效 JSON", exit_code: exitCode, stderr: stderr.trim().slice(-4000) } }
}

export default tool({
  description: "以 origin 遠端預設分支最新版本建立 Javadoc 專用分支與共用 worktree，並列出每個 Java 檔案需審查的宣告。只支援 Maven 標準 src/main/java 目錄。",
  args: {
    target_path: tool.schema.string().trim().optional().describe("不填表示整個 Maven 專案；填入時只處理一個專案相對 Java 路徑，可含開頭 @。"),
  },
  async execute(args, context) {
    return JSON.stringify(await runBackend(context.worktree, args, context.abort))
  },
})
