import type { Plugin } from "@opencode-ai/plugin"
import { tool } from "@opencode-ai/plugin"
import path from "node:path"

type BackendResult = Record<string, unknown>

const WORKER_TIMEOUT_MS = 10 * 60 * 1_000

type FileResult = {
  path: string
  status: "completed" | "failed"
  message?: string
  blocked_declarations?: Array<{
    line: number
    name: string
    reason: string
  }>
}

function message(error: unknown): string {
  if (error instanceof Error) return error.message
  return String(error)
}

async function runBackend(
  root: string,
  script: string,
  input: unknown,
  abort: AbortSignal,
): Promise<BackendResult> {
  const child = Bun.spawn(
    [
      "uv",
      "run",
      "--script",
      path.join(root, ".opencode", "tools", script),
      "--repo",
      root,
    ],
    {
      cwd: root,
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

  const cancel = () => child.kill("SIGTERM")
  if (abort.aborted) cancel()
  else abort.addEventListener("abort", cancel, { once: true })
  try {
    const [stdout, stderr, exitCode] = await Promise.all([
      new Response(child.stdout).text(),
      new Response(child.stderr).text(),
      child.exited,
    ])
    try {
      return JSON.parse(stdout.trim())
    } catch {
      return {
        status: abort.aborted ? "cancelled" : "tool-error",
        message: abort.aborted
          ? "工作已取消。"
          : `${script} 沒有回傳有效 JSON`,
        exit_code: exitCode,
        stderr: stderr.trim().slice(-4_000),
      }
    }
  } finally {
    abort.removeEventListener("abort", cancel)
  }
}

function extractJson(text: string): unknown {
  const fenced = text.match(/```(?:json)?\s*([\s\S]*?)```/i)
  const candidate = fenced?.[1] ?? text.slice(text.indexOf("{"), text.lastIndexOf("}") + 1)
  return JSON.parse(candidate.trim())
}

export function parseWorkerResult(text: string, expectedPath: string): FileResult {
  let value: unknown
  try {
    value = extractJson(text)
  } catch (error) {
    return {
      path: expectedPath,
      status: "failed",
      message: `子代理未回傳有效 JSON：${message(error)}`,
    }
  }
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return { path: expectedPath, status: "failed", message: "子代理結果不是物件" }
  }
  const result = value as Record<string, unknown>
  if (result.path !== expectedPath) {
    return { path: expectedPath, status: "failed", message: "子代理回傳路徑不符" }
  }
  if (result.status === "failed") {
    return {
      path: expectedPath,
      status: "failed",
      message:
        typeof result.message === "string" && result.message.trim()
          ? result.message.trim()
          : "逐檔子代理未完成",
    }
  }
  if (result.status !== "completed") {
    return { path: expectedPath, status: "failed", message: "子代理狀態不合法" }
  }
  const blocked = result.blocked_declarations ?? []
  if (!Array.isArray(blocked)) {
    return { path: expectedPath, status: "failed", message: "規格衝突格式不合法" }
  }
  const normalized = []
  for (const item of blocked) {
    if (
      !item ||
      typeof item !== "object" ||
      Array.isArray(item) ||
      !Number.isInteger((item as Record<string, unknown>).line) ||
      typeof (item as Record<string, unknown>).name !== "string" ||
      typeof (item as Record<string, unknown>).reason !== "string"
    ) {
      return { path: expectedPath, status: "failed", message: "規格衝突格式不合法" }
    }
    const conflict = item as { line: number; name: string; reason: string }
    normalized.push({
      line: conflict.line,
      name: conflict.name,
      reason: conflict.reason,
    })
  }
  return {
    path: expectedPath,
    status: "completed",
    blocked_declarations: normalized,
  }
}

const JavadocOrchestrator: Plugin = async ({ client, directory }) => ({
  tool: {
    run_javadocs: tool({
      description:
        "執行完整 Maven Javadoc 流程。scope=repository 處理整個專案；scope=file 只處理 target_path。",
      args: {
        scope: tool.schema.enum(["repository", "file"]),
        target_path: tool.schema.string().min(1).optional(),
      },
      async execute(args, context) {
        if (args.scope === "repository" && args.target_path !== undefined) {
          return JSON.stringify({
            status: "tool-error",
            message: "scope=repository 時不得提供 target_path",
          })
        }
        if (args.scope === "file" && args.target_path === undefined) {
          return JSON.stringify({
            status: "tool-error",
            message: "scope=file 時必須提供 target_path",
          })
        }
        const root = context.worktree || directory
        context.metadata({
          title:
            args.scope === "file"
              ? `Javadoc: ${args.target_path}`
              : "Javadoc: entire project",
        })

        const prepared = await runBackend(
          root,
          "prepare_javadoc_workspace.py",
          args.scope === "file" ? { target_path: args.target_path } : {},
          context.abort,
        )
        if (prepared.status !== "prepared") return JSON.stringify(prepared)

        const worktree = prepared.worktree
        const files = prepared.files
        if (
          typeof worktree !== "string" ||
          !Array.isArray(files) ||
          files.some(
            (file) =>
              !file ||
              typeof file !== "object" ||
              typeof (file as Record<string, unknown>).path !== "string",
          )
        ) {
          return JSON.stringify({ status: "tool-error", message: "準備工具回傳格式不合法" })
        }
        const workerDirectory = path.resolve(root, worktree)
        if (path.dirname(workerDirectory) !== path.resolve(root, "javadoc-worktrees")) {
          return JSON.stringify({ status: "tool-error", message: "子代理 worktree 路徑不合法" })
        }

        const api = client as any
        const activeSessions = new Set<string>()
        const sessions: Array<{ path: string; session_id?: string }> = []
        const abortChildren = () => {
          for (const sessionID of activeSessions) {
            void api.session.abort({
              path: { id: sessionID },
              query: { directory: workerDirectory },
            })
          }
        }
        context.abort.addEventListener("abort", abortChildren, { once: true })

        const runWorker = async (file: { path: string }): Promise<FileResult> => {
          let sessionID: string | undefined
          try {
            const created = await api.session.create({
              body: {
                parentID: context.sessionID,
                title: `Javadoc: ${file.path}`,
              },
              query: { directory: workerDirectory },
            })
            if (created.error || !created.data?.id) {
              throw new Error(`無法建立子工作階段：${message(created.error)}`)
            }
            sessionID = created.data.id
            activeSessions.add(sessionID)
            sessions.push({ path: file.path, session_id: sessionID })

            let timeout: ReturnType<typeof setTimeout> | undefined
            const response = await Promise.race([
              api.session.prompt({
                path: { id: sessionID },
                query: { directory: workerDirectory },
                body: {
                  agent: "javadoc-worker",
                  model: {
                    providerID: "openrouter",
                    modelID: "qwen/qwen3.6-35b-a3b",
                  },
                  parts: [
                    {
                      type: "text",
                      text: `worktree: ${worktree}\npath: ${file.path}`,
                    },
                  ],
                },
              }),
              new Promise<never>((_, reject) => {
                timeout = setTimeout(() => {
                  void api.session.abort({
                    path: { id: sessionID },
                    query: { directory: workerDirectory },
                  })
                  reject(new Error("子代理執行超過 10 分鐘"))
                }, WORKER_TIMEOUT_MS)
              }),
            ]).finally(() => {
              if (timeout !== undefined) clearTimeout(timeout)
            })
            if (response.error || !response.data) {
              throw new Error(`子代理執行失敗：${message(response.error)}`)
            }
            const output = response.data.parts
              .filter((part: any) => part.type === "text" && typeof part.text === "string")
              .map((part: any) => part.text)
              .join("\n")
            return parseWorkerResult(output, file.path)
          } catch (error) {
            return { path: file.path, status: "failed", message: message(error) }
          } finally {
            if (sessionID) activeSessions.delete(sessionID)
          }
        }

        let fileResults: FileResult[]
        try {
          fileResults = await Promise.all(
            (files as Array<{ path: string }>).map((file) => runWorker(file)),
          )
        } finally {
          context.abort.removeEventListener("abort", abortChildren)
        }

        const validation = await runBackend(
          root,
          "validate_javadocs.py",
          { worktree, file_results: fileResults },
          context.abort,
        )
        if (validation.status !== "validated") {
          return JSON.stringify({
            status: "validation-failed",
            prepared,
            sessions,
            file_results: fileResults,
            validation,
          })
        }

        const publication = await runBackend(
          root,
          "publish_javadocs.py",
          { worktree, publisher: "github" },
          context.abort,
        )
        return JSON.stringify({
          status: publication.status === "published" ? "published" : publication.status,
          prepared,
          sessions,
          file_results: fileResults,
          validation,
          publication,
        })
      },
    }),
  },
})

export default JavadocOrchestrator
