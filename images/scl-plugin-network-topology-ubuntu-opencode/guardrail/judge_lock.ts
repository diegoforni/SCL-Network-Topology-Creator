/**
 * Process-wide FIFO serializer for guardrail judge adjudication.
 *
 * Each adjudication uses a fresh judge session, while this lock remains the
 * correctness backstop against concurrent prompt/result crossing.
 */
let judgeChain: Promise<unknown> = Promise.resolve()

export function withJudgeLock<T>(task: () => Promise<T>): Promise<T> {
  const result = judgeChain.then(task, task)
  judgeChain = result.then(
    () => undefined,
    () => undefined,
  )
  return result
}

/** Test-only: reset the chain so unit tests start from a clean lock state. */
export function __resetJudgeLockForTest(): void {
  judgeChain = Promise.resolve()
}
