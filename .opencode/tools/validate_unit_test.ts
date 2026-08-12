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
      path.join(import.meta.dir, "validate_unit_test.py"),
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
      message: wasCancelled ? "工作已取消。" : "驗證工具沒有回傳有效 JSON",
      exit_code: exitCode,
      stderr: stderr.trim().slice(-4000),
    }
  }
}

const caseText = (description: string) =>
  tool.schema.string().trim().min(1).max(4000).describe(description)

export default tool({
  description:
    "在 prepare 建立的 detached worktree 中驗證單一 Java 型別測試檔，核對 Maven、Surefire 與 JaCoCo。",
  args: {
    target_class: tool.schema
      .string()
      .trim()
      .regex(/^[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)+$/),
    worktree: tool.schema
      .string()
      .trim()
      .regex(/^unit-test-worktrees\/[^/]+$/),
    test_cases: tool.schema
      .array(
        tool.schema.object({
          id: tool.schema.string().trim().regex(/^UT-[0-9]{3,}$/),
          scenario: caseText("測試情境、輸入與前置條件"),
          expected: caseText("可觀察的預期結果"),
          evidence: caseText("外部規格位置，或以「目前實作：」標示的行為依據"),
        }),
      )
      .min(1)
      .max(50),
  },
  async execute(args, context) {
    if (context.agent !== "unit-test") {
      return JSON.stringify({
        status: "blocked",
        message: `validate_unit_test 只允許 unit-test 子代理使用，目前代理為 ${context.agent}`,
      })
    }
    return JSON.stringify(
      await runBackend(context.worktree, args, context.abort),
    )
  },
})
