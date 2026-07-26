import { tool } from "@opencode-ai/plugin"
import path from "node:path"

type BackendResult = {
  status?: string
  [key: string]: unknown
}

const javadocText = tool.schema
  .string()
  .min(1)
  .max(50000)
  .describe("Javadoc 內文，不要包含 /**、每行開頭的 * 或結尾 */")

function encode(value: string): string {
  return Buffer.from(value, "utf8").toString("base64")
}

async function runBackend(
  projectRoot: string,
  input: {
    path: string
    additions: Array<{ target_line: number; javadoc: string }>
  },
): Promise<BackendResult> {
  const backend = path.join(import.meta.dir, "JavadocEditBackend.java")
  const payload = [
    encode(input.path),
    String(input.additions.length),
    ...input.additions.map(
      (addition) => `${addition.target_line}\t${encode(addition.javadoc)}`,
    ),
    "",
  ].join("\n")

  const process = Bun.spawn(
    ["java", "--source", "17", backend, "--repo", projectRoot],
    {
      cwd: projectRoot,
      env: { ...Bun.env, CI: "true", TERM: "dumb" },
      stdin: "pipe",
      stdout: "pipe",
      stderr: "pipe",
    },
  )
  process.stdin.write(payload)
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
      code: "BACKEND_OUTPUT_ERROR",
      message: "Javadoc 後端沒有回傳有效 JSON",
      retryable: false,
      written: false,
      exit_code: exitCode,
      stderr: stderr.trim().slice(-4000),
    }
  }
}

export default tool({
  description:
    "只在既有 src/main/java/**/*.java 宣告前新增 Javadoc。從根目錄 pom.xml 讀取 Java 8、17 或 21；每次處理一個檔案。target_line 必須是目前檔案中類別、介面、enum、record、方法、建構子或欄位宣告的第一行（有 annotation 時使用第一個 annotation 的行號）。已有 Javadoc、非宣告位置、其他路徑或任何非 Javadoc 變更都會拒絕。",
  args: {
    path: tool.schema
      .string()
      .min(1)
      .describe("src/main/java/** 下既有 .java 檔案的專案相對路徑"),
    additions: tool.schema
      .array(
        tool.schema.object({
          target_line: tool.schema
            .number()
            .int()
            .positive()
            .describe("目前檔案中目標宣告第一行的 1-based 行號"),
          javadoc: javadocText,
        }),
      )
      .min(1)
      .max(100)
      .describe("同一 Java 檔案要新增的 Javadoc；所有項目通過才會寫入"),
  },
  async execute(args, context) {
    if (context.agent !== "javadoc-writer") {
      return JSON.stringify({
        status: "blocked",
        code: "AGENT_NOT_ALLOWED",
        message: `javadoc_edit 只允許 javadoc-writer 代理使用，目前代理為 ${context.agent}`,
        retryable: false,
        written: false,
      })
    }

    const result = await runBackend(context.directory, args)
    return JSON.stringify(result)
  },
})
