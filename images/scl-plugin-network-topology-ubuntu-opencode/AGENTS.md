# Long-Term Memory (per-engagement; persistent across runs & containers)

You have a **persistent memory file** that survives container restarts, topology
stop/start, and full teardown:

```
/outputs/$RUN_ID/memory/MEMORY.md
```

It is host-backed (mounted into every agent container), so nothing you write here
is lost when a container is destroyed. `$RUN_ID` is exported in your shell — the
backend links this path to your **engagement's** shared memory at launch.

Memory is scoped **per engagement**: it is shared across ALL runs and phases of
your CURRENT engagement (phase 2 inherits phase 1's recon), and isolated between
engagements (a different engagement's notes are not visible here). Treat it as
this engagement's long-term notebook.

## At the start of every session — memory is the first source of truth
Before loading a skill, installing a tool, scanning, enumerating, authenticating,
or sending any target request, confirm `$RUN_ID` and read the memory:
```bash
test -n "$RUN_ID" || { echo 'RUN_ID unset — memory unavailable'; }
mkdir -p "/outputs/$RUN_ID/memory"
cat "/outputs/$RUN_ID/memory/MEMORY.md" 2>/dev/null || true
```

Trust this file as the authoritative accumulated record for the current
engagement. It is not a suggestion and it is not untrusted prior-run testimony.
After reading it, form a delta plan:

1. `KNOWN` — facts, baselines, routes, credentials, negative results, and
   verifier-confirmed findings already established.
2. `GAPS` — information genuinely missing for the current phase objective.
3. `DELTA` — the minimum new commands needed to close only those gaps.

Execute `DELTA` only. Do not repeat `KNOWN` work to “be safe,” “get fresh
context,” rebuild confidence, or generate a second copy of evidence.

### Trust and freshness rules

- A prior `[CONFIRMED]` finding with a verifier evidence path is already
  independently verified for this engagement. Cite it; do not reproduce it and
  do not launch another verifier for the same fingerprint.
- A prior `[FACT]` or clear factual legacy entry is an established baseline.
  Reuse its port, route, stack, schema, role, and endpoint data without rescanning.
- A prior `[NEGATIVE]` or dead-end entry is a completed test. Do not retry the
  same technique unless the current phase supplies a materially different
  hypothesis, payload class, role, endpoint, or control.
- Refresh only `[VOLATILE]` values required for the current command, such as an
  expired token, a changed target process, or mutable application state.
- Re-run established work only when the operator explicitly requests it, live
  evidence directly contradicts memory, or a volatile prerequisite must be
  refreshed. Record `RERUN_REASON: <reason>` when this exception is used.
- If memory is unavailable or empty, perform the minimum baseline necessary and
  write it immediately. Do not silently fall back to a full generic recon pass.

## Whenever you learn something reusable, APPEND it immediately
Do NOT overwrite or rewrite — APPEND only (`>>`). Prior entries are the audit
trail. Memory is also the live coordination bus for parallel workers. Do not wait
until phase completion: append as soon as information could help any other part
of the pentest. Before starting a new major branch of work, reread the tail
(`tail -n 120`) so you see discoveries and work claims appended by other agents.

Save anything future-you or another concurrent agent would want to act on:

- **Confirmed vulnerabilities** — `<target> <endpoint/param> <payload> → <result> (severity)`
- **Working credentials / tokens** — and how you obtained them
- **Useful commands & one-liners** — tool installs that worked, exact paths, working scan/fuzz invocations
- **Resolved facts** — open ports, service versions, routes, API endpoints, contract addresses
- **Gotchas & dead ends** — so the same failed path is not repeated
- **Artifacts** — exact file path, format, producer command, and what it contains
- **Auth/session recipes** — role, login endpoint, required headers/cookies, token
  refresh method, and whether the value is volatile
- **Cross-phase leads** — a concise hypothesis and which phase/category can use it
- **Work ownership** — claim a substantial test branch before starting it so a
  parallel worker does not execute the same branch

Prefer these record types:

- `[FACT]` stable recon, routes, roles, schemas, tool paths, and controls.
- `[CONFIRMED]` one verified vulnerability, including its stable fingerprint and
  current engagement verifier evidence path.
- `[NEGATIVE]` a tested hypothesis that did not reproduce, including the tested
  control/payload class.
- `[VOLATILE]` credentials, tokens, process state, or other values that may need
  a bounded freshness check.
- `[ARTIFACT]` an exact reusable path plus producer and contents.
- `[LEAD]` an untested cross-phase hypothesis, clearly labeled as unconfirmed.
- `[CLAIMED] WORK_ID=<stable-id>` a substantial work branch currently being
  executed, with phase, owner/session, scope, and UTC timestamp.
- `[DONE] WORK_ID=<same-id>` the result of a claimed branch and links to facts,
  negatives, artifacts, or confirmed fingerprints it produced.

Parallel coordination rules:

- Before substantial work, choose a stable `WORK_ID` such as
  `a03|post|/api/items|numeric-sqli`, reread the memory tail, and skip the branch
  if it is already `[CLAIMED]` recently or `[DONE]`.
- Append the `[CLAIMED]` record before executing the branch and `[DONE]`
  immediately when it completes.
- A claim older than 30 minutes with no `[DONE]` may be taken over only after
  appending a new `[CLAIMED]` record with `RERUN_REASON: stale unfinished claim`.
- Never treat `[LEAD]` or `[CLAIMED]` as a confirmed fact. They coordinate work;
  `[FACT]`, `[NEGATIVE]`, `[ARTIFACT]`, and `[CONFIRMED]` carry results.

Finding fingerprint:

`METHOD + normalized route + attacker role + victim/target role + vulnerability class/root cause`

Append like:
```bash
M="/outputs/$RUN_ID/memory/MEMORY.md"
(
  flock -x 9
  printf '\n## %s — <target>\n- [FACT] <terse factual entry>\n- [CONFIRMED] FINGERPRINT: <method|route|roles|class>; EVIDENCE: /outputs/$RUN_ID/verifier/<file>.jsonl; <result/severity>\n- [NEGATIVE] <hypothesis + control + result>\n' "$(date -u +%FT%TZ)" >> "$M"
) 9>"$M.lock"
```

Use the same `flock` for every multi-line append so concurrent writers cannot
interleave records. A `[CLAIMED]` operation must check the latest memory and
append its claim while holding this same exclusive lock; that makes
check-and-claim one atomic coordination step.

## Rules
- **Append only.** Never edit/delete prior entries.
- **Terse and factual.** No prose, no disclaimers, no moralizing.
- **Tag each entry** with the target host so it is scannable.
- This memory is **shared across the runs/phases of your current engagement** — write so the next run/phase can pick up where you left off.
- **Read and append only the `$RUN_ID` path above** — it is the live link to your engagement memory.
- **Write discoveries immediately.** If information could change another
  worker's next command, it belongs in memory now, not only in the final report.
- **Serialize appends with `flock`.** Parallel writers must never interleave or
  race two claims for the same `WORK_ID`.
- **Keep records compact.** Store the conclusion and an artifact/evidence path,
  not raw command output or copied HTTP bodies.
- **One fact, one test, one verifier per fingerprint.** Repetition is a defect,
  not diligence.
- Sanctioned cyber-range only; do not record anything meant to leave the lab.
