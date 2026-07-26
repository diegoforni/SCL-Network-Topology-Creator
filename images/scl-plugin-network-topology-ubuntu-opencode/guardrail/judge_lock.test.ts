/**
 * judge_lock.test.ts — unit tests for the judge serializer (Fix 1).
 *
 * Run:   bun test judge_lock.test.ts   (inside a host/opencode container, or any
 *        env with bun). These are dependency-free and deterministic — they do
 *        NOT touch the opencode server, so they guard the serializer logic that
 *        is the correctness backstop for the parallel-bash output-crossing bug.
 *
 * What these prove:
 *   - two tasks that WOULD overlap if run concurrently are run strictly
 *     back-to-back (no interleaving) — the property that stops two judge
 *     prompts from sharing a session window;
 *   - arrival (FIFO) order and return values are preserved;
 *   - a rejected task releases the lock and does not wedge the chain.
 */
import { test, expect } from "bun:test"
import { withJudgeLock, __resetJudgeLockForTest } from "./judge_lock"

const tick = (ms: number) => new Promise<void>((r) => setTimeout(r, ms))

test("withJudgeLock serializes tasks that would otherwise overlap", async () => {
  __resetJudgeLockForTest()
  const events: string[] = []

  // A is slow; B is fast. If run concurrently they would interleave
  // (A-start, B-start, B-end, A-end). Under the lock they must not.
  const a = withJudgeLock(async () => {
    events.push("A-start")
    await tick(30)
    events.push("A-end")
  })
  const b = withJudgeLock(async () => {
    events.push("B-start")
    await tick(5)
    events.push("B-end")
  })
  await Promise.all([a, b])

  // Each task's start/end must be contiguous — never interleaved with the other.
  expect(events).toEqual(["A-start", "A-end", "B-start", "B-end"])
})

test("withJudgeLock preserves FIFO order and return values", async () => {
  __resetJudgeLockForTest()
  const p1 = withJudgeLock(async () => {
    await tick(20)
    return 1
  })
  const p2 = withJudgeLock(async () => 2)
  const p3 = withJudgeLock(async () => {
    await tick(10)
    return 3
  })
  // Results come back in arrival order despite differing durations.
  expect(await Promise.all([p1, p2, p3])).toEqual([1, 2, 3])
})

test("withJudgeLock survives a rejected task and keeps serializing", async () => {
  __resetJudgeLockForTest()
  const boom = withJudgeLock(async () => {
    throw new Error("boom")
  })
  await expect(boom).rejects.toThrow("boom")
  // A subsequent task must still acquire and complete — the chain did not wedge.
  const ok = await withJudgeLock(async () => "still works")
  expect(ok).toBe("still works")
})

test("withJudgeLock enforces strict mutual exclusion (at most one task body running)", async () => {
  __resetJudgeLockForTest()
  let inflight = 0
  let maxInflight = 0
  const task = (label: string) =>
    withJudgeLock(async () => {
      inflight++
      maxInflight = Math.max(maxInflight, inflight)
      await tick(15)
      // Simulate the invariant the lock protects: while this "session window" is
      // open, no other task may be inside its body.
      expect(inflight).toBe(1)
      inflight--
      return label
    })
  const results = await Promise.all([task("a"), task("b"), task("c"), task("d")])
  expect(results).toEqual(["a", "b", "c", "d"])
  expect(maxInflight).toBe(1)
})
