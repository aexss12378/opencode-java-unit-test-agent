import path from "node:path"

export type BackendResult = {
  status?: string
  [key: string]: unknown
}

export async function runUnitTestBackend(
  action: "prepare" | "bind" | "finalize" | "validate" | "submit",
  projectRoot: string,
  sessionID: string,
  input: unknown,
  abort: AbortSignal,
): Promise<BackendResult> {
  const script = path.join(import.meta.dir, "..", "tools", "submit_unit_tests.py")
  const process = Bun.spawn(
    [
      "uv",
      "run",
      "--no-project",
      "python",
      script,
      action,
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

  process.stdin.write(JSON.stringify(input))
  process.stdin.end()

  let cancelled = abort.aborted
  const cancel = () => {
    cancelled = true
    process.kill("SIGTERM")
  }
  if (cancelled) {
    cancel()
  } else {
    abort.addEventListener("abort", cancel, { once: true })
  }

  let stdout: string
  let stderr: string
  let exitCode: number
  try {
    const output = await Promise.all([
      new Response(process.stdout).text(),
      new Response(process.stderr).text(),
      process.exited,
    ])
    stdout = output[0]
    stderr = output[1]
    exitCode = output[2]
  } finally {
    abort.removeEventListener("abort", cancel)
  }

  try {
    return JSON.parse(stdout.trim()) as BackendResult
  } catch {
    if (cancelled) {
      return {
        status: "cancelled",
        message: "單元測試工作已取消；後端未完成可驗證的結果。",
        exit_code: exitCode,
        stderr: stderr.trim().slice(-4000),
      }
    }
    return {
      status: "tool-error",
      message: "單元測試後端沒有回傳有效 JSON",
      exit_code: exitCode,
      stderr: stderr.trim().slice(-4000),
    }
  }
}
