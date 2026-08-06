import path from "node:path"
import type { PluginInput } from "@opencode-ai/plugin"
import {
  runUnitTestBackend,
  type BackendResult,
} from "./unit_test_backend"

export type DispatchTarget = {
  target_class: string
  specification_sources: string[]
}

export type DispatchArguments = {
  targets: DispatchTarget[]
  max_concurrency: number
}

export type DispatchContext = {
  sessionID: string
  worktree: string
  abort: AbortSignal
}

export type UnitTestBackendRunner = typeof runUnitTestBackend

type OpenCodeClient = PluginInput["client"]

type PreparedAssignment = {
  assignment_id: string
  target_class: string
  test_file: string
  branch: string
  base_sha: string
  worktree: string
  prompt: string
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}

function requiredString(
  value: Record<string, unknown>,
  key: string,
): string {
  const field = value[key]
  if (typeof field !== "string" || field.length === 0) {
    throw new Error(`派工後端缺少有效欄位：${key}`)
  }
  return field
}

function preparedAssignments(result: BackendResult): PreparedAssignment[] {
  if (!Array.isArray(result.prepared)) {
    return []
  }
  return result.prepared.map((raw) => {
    if (!isRecord(raw)) {
      throw new Error("派工後端回傳無效的 worktree 資料")
    }
    return {
      assignment_id: requiredString(raw, "assignment_id"),
      target_class: requiredString(raw, "target_class"),
      test_file: requiredString(raw, "test_file"),
      branch: requiredString(raw, "branch"),
      base_sha: requiredString(raw, "base_sha"),
      worktree: requiredString(raw, "worktree"),
      prompt: requiredString(raw, "prompt"),
    }
  })
}

function describe(value: unknown): string {
  if (value instanceof Error) {
    return value.message
  }
  if (typeof value === "string") {
    return value
  }
  try {
    return JSON.stringify(value)
  } catch {
    return String(value)
  }
}

function requireSdkData<T>(
  response: { data?: T; error?: unknown },
  action: string,
): T {
  if (response.error !== undefined) {
    throw new Error(`${action}失敗：${describe(response.error)}`)
  }
  if (response.data === undefined) {
    throw new Error(`${action}沒有回傳資料`)
  }
  return response.data
}

function responseText(value: unknown): string {
  if (!isRecord(value) || !Array.isArray(value.parts)) {
    return ""
  }
  return value.parts
    .filter(
      (part): part is Record<string, unknown> =>
        isRecord(part) &&
        part.type === "text" &&
        typeof part.text === "string",
    )
    .map((part) => part.text as string)
    .join("\n")
    .slice(-4_000)
}

function verifiedSuccess(result: Record<string, unknown>): boolean {
  return (
    result.status === "draft-pr-created" &&
    result.pr_created === true &&
    result.pr_verified === true &&
    isRecord(result.pr) &&
    result.pr.draft === true &&
    typeof result.commit_sha === "string" &&
    result.commit_sha === result.remote_sha
  )
}

async function mapWithConcurrency<T, R>(
  values: T[],
  concurrency: number,
  worker: (value: T) => Promise<R>,
): Promise<R[]> {
  const results = new Array<R>(values.length)
  let cursor = 0
  const runners = Array.from(
    { length: Math.min(concurrency, values.length) },
    async () => {
      while (true) {
        const index = cursor
        cursor += 1
        if (index >= values.length) {
          return
        }
        results[index] = await worker(values[index])
      }
    },
  )
  await Promise.all(runners)
  return results
}

async function finalizeSafely(
  backend: UnitTestBackendRunner,
  assignment: PreparedAssignment,
  context: DispatchContext,
  workerSessionID: string | undefined,
  workerMessage: string,
  workerError: string,
): Promise<BackendResult> {
  try {
    return await backend(
      "finalize",
      assignment.worktree,
      context.sessionID,
      {
        assignment_id: assignment.assignment_id,
        worker_session_id: workerSessionID,
        worker_message: workerMessage,
        worker_error: workerError,
        cancelled: context.abort.aborted,
      },
      new AbortController().signal,
    )
  } catch (error) {
    return {
      status: "worker-finalization-failed",
      message: `無法核對子工作階段結果：${describe(error)}`,
      assignment_id: assignment.assignment_id,
      target_class: assignment.target_class,
      test_file: assignment.test_file,
      branch: assignment.branch,
      base_sha: assignment.base_sha,
      worker_session_id: workerSessionID,
      submitted: false,
      pr_created: false,
      merged: false,
      worktree_retained: true,
      worktree: assignment.worktree,
      manual_recovery_required: true,
    }
  }
}

export async function dispatchUnitTests(
  client: OpenCodeClient,
  args: DispatchArguments,
  context: DispatchContext,
  backend: UnitTestBackendRunner = runUnitTestBackend,
): Promise<BackendResult> {
  const preparation = await backend(
    "prepare",
    context.worktree,
    context.sessionID,
    args,
    context.abort,
  )
  const prepared = preparedAssignments(preparation)
  if (prepared.length === 0) {
    return preparation
  }

  const active = new Map<string, string>()
  const abortActiveSessions = () => {
    for (const [sessionID, directory] of active) {
      void client.session
        .abort({
          path: { id: sessionID },
          query: { directory },
        })
        .catch(() => undefined)
    }
  }
  context.abort.addEventListener("abort", abortActiveSessions, { once: true })

  let completed: BackendResult[]
  try {
    completed = await mapWithConcurrency(
      prepared,
      args.max_concurrency,
      async (assignment) => {
        let workerSessionID: string | undefined
        let workerMessage = ""
        let workerError = ""
        try {
          if (context.abort.aborted) {
            throw new Error("派工已取消")
          }
          const createdResponse = await client.session.create({
            body: {
              parentID: context.sessionID,
              title: `單元測試：${assignment.target_class}`,
            },
            query: { directory: assignment.worktree },
            signal: context.abort,
          })
          const child = requireSdkData(createdResponse, "建立子工作階段")
          if (child.parentID !== context.sessionID) {
            throw new Error("新工作階段沒有掛在目前主工作階段下")
          }
          if (path.resolve(child.directory) !== path.resolve(assignment.worktree)) {
            throw new Error("新工作階段沒有使用派工指定的 worktree")
          }
          workerSessionID = child.id
          active.set(workerSessionID, assignment.worktree)

          const binding = await backend(
            "bind",
            assignment.worktree,
            context.sessionID,
            {
              assignment_id: assignment.assignment_id,
              worker_session_id: workerSessionID,
            },
            context.abort,
          )
          if (binding.status !== "assignment-bound") {
            throw new Error(
              `無法綁定子工作階段：${String(binding.message ?? binding.status ?? "未知錯誤")}`,
            )
          }

          const promptResponse = await client.session.prompt({
            path: { id: workerSessionID },
            query: { directory: assignment.worktree },
            body: {
              agent: "unit-test",
              parts: [{ type: "text", text: assignment.prompt }],
            },
            signal: context.abort,
          })
          const response = requireSdkData(promptResponse, "執行子工作階段")
          workerMessage = responseText(response)
          if (response.info.sessionID !== workerSessionID) {
            throw new Error("子工作階段回應識別碼不一致")
          }
          if (response.info.error !== undefined) {
            throw new Error(`子工作階段模型錯誤：${describe(response.info.error)}`)
          }
        } catch (error) {
          workerError = describe(error).slice(-4_000)
          if (workerSessionID !== undefined) {
            await client.session
              .abort({
                path: { id: workerSessionID },
                query: { directory: assignment.worktree },
              })
              .catch(() => undefined)
          }
        } finally {
          if (workerSessionID !== undefined) {
            active.delete(workerSessionID)
          }
        }
        return finalizeSafely(
          backend,
          assignment,
          context,
          workerSessionID,
          workerMessage,
          workerError,
        )
      },
    )
  } finally {
    context.abort.removeEventListener("abort", abortActiveSessions)
  }

  const initial = Array.isArray(preparation.results)
    ? preparation.results.filter(isRecord)
    : []
  const indexed = new Map<string, Record<string, unknown>>()
  for (const result of [...initial, ...completed]) {
    if (typeof result.target_class === "string") {
      indexed.set(result.target_class, result)
    }
  }
  const targetOrder = Array.isArray(preparation.target_order)
    ? preparation.target_order.filter(
        (value): value is string => typeof value === "string",
      )
    : args.targets.map((target) => target.target_class).sort()
  const results = targetOrder.map(
    (targetClass) =>
      indexed.get(targetClass) ?? {
        status: "worker-finalization-failed",
        message: "派工結果遺失",
        target_class: targetClass,
        submitted: false,
        pr_created: false,
        merged: false,
        manual_recovery_required: true,
      },
  )
  const successCount = results.filter(verifiedSuccess).length
  const overall =
    successCount === results.length
      ? "completed"
      : context.abort.aborted
        ? "cancelled"
        : "partial-failure"
  return {
    status: overall,
    message: `${results.length} 個 Service 已結束：${successCount} 個建立並驗證 Draft PR，${results.length - successCount} 個未完成。`,
    dispatched: true,
    parent_session_id: context.sessionID,
    base_branch: preparation.base_branch,
    base_sha: preparation.base_sha,
    max_concurrency: args.max_concurrency,
    service_count: results.length,
    child_session_count: results.filter(
      (result) => typeof result.worker_session_id === "string",
    ).length,
    draft_pr_count: successCount,
    results,
  }
}
