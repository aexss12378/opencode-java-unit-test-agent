import { tool } from "@opencode-ai/plugin"
import path from "node:path"

const caseText = (description: string) =>
  tool.schema.string().trim().min(1).max(4000).describe(description)

type BackendResult = {
  status?: string
  [key: string]: unknown
}

async function runBackend(
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
        message: "單元測試工作已取消；後端未完成提交或 PR 建立結果。",
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

export default tool({
  description:
    "提交單一 Java 候選測試。工具為目前子工作建立獨立 Git worktree 與分支，另以不含 .git 的短暫副本執行候選測試並確認目標類別行覆蓋率至少 80%；通過後只提交測試檔、推送分支並建立 Draft PR，絕不自動合併。",
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
    file: tool.schema.object({
      path: tool.schema
        .string()
        .describe("src/test/java/** 下以 Test.java 結尾的測試檔路徑"),
      content: tool.schema
        .string()
        .min(1)
        .max(100000)
        .describe("完整 Java 候選測試原始碼"),
    }),
  },
  async execute(args, context) {
    if (context.agent !== "unit-test") {
      return JSON.stringify({
        status: "blocked",
        message: `submit_unit_tests 只允許 unit-test 代理使用，目前代理為 ${context.agent}`,
      })
    }

    return JSON.stringify(
      await runBackend(context.worktree, context.sessionID, args, context.abort),
    )
  },
})
