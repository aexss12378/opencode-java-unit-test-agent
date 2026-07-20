import { tool } from "@opencode-ai/plugin"
import { rm } from "node:fs/promises"
import path from "node:path"

const caseText = (description: string) =>
  tool.schema.string().trim().min(1).max(4000).describe(description)

type BackendResult = {
  status?: string
  [key: string]: unknown
}

async function runBackend(
  projectRoot: string,
  action: "review" | "publish",
  input: unknown,
): Promise<BackendResult> {
  const script = path.join(import.meta.dir, "submit_unit_tests.py")
  const process = Bun.spawn(
    [
      "python3",
      script,
      action,
      "--repo",
      projectRoot,
    ],
    {
      cwd: projectRoot,
      env: { ...Bun.env, CI: "true", TERM: "dumb" },
      stdin: "pipe",
      stdout: "pipe",
      stderr: "pipe",
    },
  )

  process.stdin.write(JSON.stringify(input))
  process.stdin.end()

  const [stdout, stderr, exitCode] = await Promise.all([
    new Response(process.stdout).text(),
    new Response(process.stderr).text(),
    process.exited,
  ])
  try {
    return JSON.parse(stdout.trim()) as BackendResult
  } catch {
    return {
      status: "tool-error",
      message: "單元測試後端沒有回傳有效 JSON",
      exit_code: exitCode,
      stderr: stderr.trim().slice(-4000),
    }
  }
}

export default tool({
  description: "提交單一 Java 候選測試。工具先在短暫副本執行 Maven test 並確認候選測試真的有執行，通過後建立 IDE 審查資料並等待人工核准；核准後才寫入 src/test/java/**。",
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

    const projectRoot = context.directory
    const review = await runBackend(projectRoot, "review", args)
    if (review.status !== "awaiting-approval") {
      return JSON.stringify(review)
    }

    try {
      await context.ask({
        permission: "unit_test_submission",
        patterns: [".opencode/unit-test-review/", args.file.path],
        always: [],
        metadata: {
          title: "請先在 IDE 審查候選單元測試",
          review_directory: review.review_directory,
        },
      })
    } catch {
      await rm(path.join(projectRoot, ".opencode/unit-test-review"), {
        recursive: true,
        force: true,
      })
      return JSON.stringify({
        status: "rejected",
        message: "工程師已拒絕候選測試；沒有寫入正式測試目錄。",
        published: false,
      })
    }

    const publication = await runBackend(projectRoot, "publish", args)
    if (publication.status !== "published") {
      await rm(path.join(projectRoot, ".opencode/unit-test-review"), {
        recursive: true,
        force: true,
      })
    }
    return JSON.stringify({ ...publication, validation: review.validation })
  },
})
