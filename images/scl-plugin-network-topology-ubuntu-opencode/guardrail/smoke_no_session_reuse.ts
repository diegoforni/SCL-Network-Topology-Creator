/**
 * smoke_no_session_reuse.ts — in-container regression check for Fix 2.
 *
 * Validates the mechanism that stops parallel-bash output crossing at the
 * source: that creating judge sessions with NO title returns DISTINCT sessions,
 * so concurrent commands can never share one session. (The running image reused
 * one titled session per executor session; every command appended to it, and
 * concurrent prompts crossed.)
 *
 * Run INSIDE a topology host container (where 127.0.0.1:4097 is the judge):
 *   bun run smoke_no_session_reuse.ts
 *   bun run smoke_no_session_reuse.ts --base http://127.0.0.1:4097
 * Exits 0 on success, 1 on failure.
 *
 * NOTE: this checks session ISOLATION (policy-free, deterministic). The full
 * end-to-end "two memory reads return their own stdout" check is a manual
 * post-deploy step — see the comment block at the bottom of this file.
 */
import { createSession, abortSession } from "./guardrail_client"

const BASE = (() => {
  const i = process.argv.indexOf("--base")
  return i > 0 && process.argv[i + 1] ? process.argv[i + 1] : "http://127.0.0.1:4097"
})()

function fail(msg: string): never {
  console.error(`❌ FAIL: ${msg}`)
  process.exit(1)
}

async function main() {
  console.log(`smoke: judge base = ${BASE}`)

  // --- TEST: concurrent UNTITLED session creation must yield DISTINCT ids -----
  // If opencode ever reuses a session here, parallel commands would share it and
  // the sync /message RPC could cross their verdicts/stdout again.
  const N = 8
  const ids = await Promise.all(
    Array.from({ length: N }, () => createSession(BASE, undefined, { timeoutMs: 30000 })),
  )
  const missing = ids.filter((id) => !id)
  if (missing.length) fail(`createSession returned null ${missing.length}/${N} times — is the judge up on ${BASE}?`)

  const distinct = new Set(ids as string[])
  console.log(`created ${N} untitled sessions concurrently -> ${distinct.size} distinct ids`)
  if (distinct.size !== N) {
    fail(
      `expected ${N} distinct session ids, got ${distinct.size}. ` +
        `Session reuse is back — concurrent bash commands WILL cross outputs. ` +
        `Ensure extractVerdictWithRetries passes NO title to createSession.`,
    )
  }

  // Cleanup the sessions we created (best-effort) so the run doesn't accumulate.
  await Promise.all((ids as string[]).map((id) => abortSession(BASE, id, { timeoutMs: 5000 }).catch(() => {})))

  console.log("✅ PASS: untitled judge sessions are not reused — parallel commands get isolated sessions.")
}

main().catch((err) => fail(`unexpected error: ${String(err)}`))

/*
 * MANUAL END-TO-END CHECK (post-deploy) — the real "two parallel reads" test.
 *
 * The deterministic unit + smoke tests above guard the mechanism. To confirm the
 * agent itself now sees correct outputs when it batches parallel bash calls:
 *
 *   1. Start a topology host (or coder56 sandbox) on the rebuilt image.
 *   2. Launch a coder56 run whose Phase 1 prompt makes the agent read engagement
 *      memory (`cat $RUN_ID/memory/MEMORY.md`) IN THE SAME TURN as a
 *      reachability check (`ping`/`curl`) — i.e. a parallel bash batch, exactly
 *      the shape that triggered the bug.
 *   3. After the turn, inspect the guardrail logs:
 *        /outputs/<run>/guardrail/sessions/*.jsonl   (per-turn judge raw_response_text)
 *        /outputs/<run>/guardrail/verdicts.ndjson     (per-command decisions)
 *      and the executor transcript (opencode.db parts for the bash tool calls).
 *
 *   PASS criteria:
 *     - The memory-read command's stored tool output is the MEMORY FILE CONTENTS
 *       (the seeded header / prior recon), NOT the ping/curl output.
 *     - Each guardrail session id is used for ONE command only (no 32+ message
 *       accumulation in a single session). grep the judge DB:
 *         sqlite3 /root/.guardrail-home/.local/share/opencode/opencode.db \
 *           "SELECT session_id, COUNT(*) FROM message GROUP BY session_id"
 *       Every session should now have a small, bounded message count.
 *     - Each turn's judge raw_response_text reasons about ITS OWN command.
 *
 *   Before the fix, all three failed: the memory read returned ping/nmap output,
 *   one session held 83 messages, and the judge reasoned about sibling commands.
 */
