import { describe, expect, test } from "bun:test"
import {
  dispatchUnitTests,
  type UnitTestBackendRunner,
} from "../lib/unit_test_dispatch"

type Call = {
  action: string
  options: Record<string, any>
}

function prepared(count: number) {
  return Array.from({ length: count }, (_, index) => ({
    assignment_id: `assignment-${index}`,
    target_class: `com.example.Service${index}Service`,
    test_file: `src/test/java/com/example/Service${index}ServiceTest.java`,
    branch: `opencode/unit-test/service-${index}`,
    base_sha: "a".repeat(40),
    worktree: `/tmp/worktree-${index}`,
    prompt: `只處理 Service${index}Service`,
  }))
}

function backendFor(assignments: ReturnType<typeof prepared>) {
  const calls: Call[] = []
  const backend = (async (
    action: string,
    projectRoot: string,
    sessionID: string,
    input: Record<string, any>,
  ) => {
    calls.push({ action, options: { projectRoot, sessionID, input } })
    if (action === "prepare") {
      return {
        status: "prepared",
        base_branch: "main",
        base_sha: "a".repeat(40),
        target_order: assignments.map((item) => item.target_class),
        prepared: assignments,
        results: [],
      }
    }
    if (action === "bind") {
      return {
        status: "assignment-bound",
        assignment_id: input.assignment_id,
        worker_session_id: input.worker_session_id,
      }
    }
    if (action === "finalize") {
      const assignment = assignments.find(
        (item) => item.assignment_id === input.assignment_id,
      )!
      const sha = String(input.worker_session_id).padEnd(40, "0").slice(0, 40)
      return {
        status: "draft-pr-created",
        target_class: assignment.target_class,
        test_file: assignment.test_file,
        branch: assignment.branch,
        base_sha: assignment.base_sha,
        worker_session_id: input.worker_session_id,
        pr_created: true,
        pr_verified: true,
        pr: { draft: true, url: "https://example.invalid/pr" },
        commit_sha: sha,
        remote_sha: sha,
        worktree_retained: false,
      }
    }
    throw new Error(`未預期的後端動作：${action}`)
  }) as UnitTestBackendRunner
  return { backend, calls }
}

describe("unit-test 子工作階段分派", () => {
  test("每個 Service 都以主工作階段為 parent 並使用自己的 worktree", async () => {
    const assignments = prepared(2)
    const { backend, calls: backendCalls } = backendFor(assignments)
    const sdkCalls: Call[] = []
    let nextSession = 0
    const client = {
      session: {
        create: async (options: Record<string, any>) => {
          sdkCalls.push({ action: "create", options })
          const id = `child-${nextSession++}`
          return {
            data: {
              id,
              parentID: options.body.parentID,
              directory: options.query.directory,
            },
          }
        },
        prompt: async (options: Record<string, any>) => {
          sdkCalls.push({ action: "prompt", options })
          return {
            data: {
              info: { sessionID: options.path.id },
              parts: [{ type: "text", text: "完成" }],
            },
          }
        },
        abort: async (options: Record<string, any>) => {
          sdkCalls.push({ action: "abort", options })
          return { data: true }
        },
      },
    }

    const result = await dispatchUnitTests(
      client as any,
      {
        targets: assignments.map((item) => ({
          target_class: item.target_class,
          specification_sources: ["docs/spec.md"],
        })),
        max_concurrency: 2,
      },
      {
        sessionID: "main-session",
        worktree: "/repo",
        abort: new AbortController().signal,
      },
      backend,
    )

    expect(result.status).toBe("completed")
    expect(result.child_session_count).toBe(2)
    const creates = sdkCalls.filter((call) => call.action === "create")
    expect(creates).toHaveLength(2)
    for (const [index, call] of creates.entries()) {
      expect(call.options.body.parentID).toBe("main-session")
      expect(call.options.query.directory).toBe(assignments[index].worktree)
    }
    const prompts = sdkCalls.filter((call) => call.action === "prompt")
    expect(prompts).toHaveLength(2)
    for (const [index, call] of prompts.entries()) {
      expect(call.options.body.agent).toBe("unit-test")
      expect(call.options.query.directory).toBe(assignments[index].worktree)
    }
    expect(backendCalls.filter((call) => call.action === "bind")).toHaveLength(2)
    expect(backendCalls.some((call) => call.action === "dispatch")).toBeFalse()
  })

  test("同時執行的子代理不超過 max_concurrency", async () => {
    const assignments = prepared(6)
    const { backend } = backendFor(assignments)
    let nextSession = 0
    let active = 0
    let maximumActive = 0
    const client = {
      session: {
        create: async (options: Record<string, any>) => ({
          data: {
            id: `child-${nextSession++}`,
            parentID: options.body.parentID,
            directory: options.query.directory,
          },
        }),
        prompt: async (options: Record<string, any>) => {
          active += 1
          maximumActive = Math.max(maximumActive, active)
          await Bun.sleep(10)
          active -= 1
          return {
            data: {
              info: { sessionID: options.path.id },
              parts: [{ type: "text", text: "完成" }],
            },
          }
        },
        abort: async () => ({ data: true }),
      },
    }

    const result = await dispatchUnitTests(
      client as any,
      {
        targets: assignments.map((item) => ({
          target_class: item.target_class,
          specification_sources: ["docs/spec.md"],
        })),
        max_concurrency: 3,
      },
      {
        sessionID: "main-session",
        worktree: "/repo",
        abort: new AbortController().signal,
      },
      backend,
    )

    expect(result.status).toBe("completed")
    expect(maximumActive).toBe(3)
  })
})
