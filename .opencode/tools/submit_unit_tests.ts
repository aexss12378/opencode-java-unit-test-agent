import { tool } from "@opencode-ai/plugin"
import path from "node:path"

type BackendResult = {
  status?: string
  [key: string]: unknown
}

async function runUnitTestBackend(
  projectRoot: string,
  sessionID: string,
  input: unknown,
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
      "submit",
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

const caseText = (description: string) =>
  tool.schema.string().trim().min(1).max(4000).describe(description)

export default tool({
  description:
    "發布目前派工 worktree 內已完成的唯一 Service 測試檔。工具會重新執行 Maven 與 JaCoCo 驗證、只提交派工測試檔、推送既定分支、建立 Draft PR，並核對遠端 SHA 與 PR 狀態；不建立 worktree、不接受檔案內容，也絕不合併。",
  args: {
    test_cases: tool.schema
      .array(
        tool.schema.object({
          id: tool.schema
            .string()
            .trim()
            .regex(/^UT-[0-9]{3,}$/)
            .describe("測試案例編號，例如 UT-001"),
          scenario: caseText("測試情境、輸入與前置條件"),
          expected: caseText("可觀察的預期結果"),
          evidence: caseText("專案檔案位置或工程師提供的需求"),
        }),
      )
      .min(1)
      .max(50),
  },
  async execute(args, context) {
    if (context.agent !== "unit-test") {
      return JSON.stringify({
        status: "blocked",
        message: `submit_unit_tests 只允許 unit-test 工作代理使用，目前代理為 ${context.agent}`,
      })
    }
    return JSON.stringify(
      await runUnitTestBackend(
        context.worktree,
        context.sessionID,
        args,
        context.abort,
      ),
    )
  },
})
