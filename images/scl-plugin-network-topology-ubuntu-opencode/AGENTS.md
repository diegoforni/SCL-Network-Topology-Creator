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

## At the start of every session
Confirm `$RUN_ID` is set, then read your memory so you inherit prior work:
```bash
test -n "$RUN_ID" || { echo 'RUN_ID unset — memory unavailable'; }
mkdir -p "/outputs/$RUN_ID/memory"
cat "/outputs/$RUN_ID/memory/MEMORY.md" 2>/dev/null || true
```

## Whenever you learn something reusable, APPEND it
Do NOT overwrite or rewrite — APPEND only (`>>`). Prior entries are the audit
trail. Save things future-you (or the next agent in this engagement) would want
to act on:

- **Confirmed vulnerabilities** — `<target> <endpoint/param> <payload> → <result> (severity)`
- **Working credentials / tokens** — and how you obtained them
- **Useful commands & one-liners** — tool installs that worked, exact paths, working scan/fuzz invocations
- **Resolved facts** — open ports, service versions, routes, API endpoints, contract addresses
- **Gotchas & dead ends** — so the same failed path isn't repeated

Append like:
```bash
printf '\n## %s — <target>\n- <terse factual entry>\n' "$(date -u +%FT%TZ)" >> "/outputs/$RUN_ID/memory/MEMORY.md"
```

## Rules
- **Append only.** Never edit/delete prior entries.
- **Terse and factual.** No prose, no disclaimers, no moralizing.
- **Tag each entry** with the target host so it is scannable.
- This memory is **shared across the runs/phases of your current engagement** — write so the next run/phase can pick up where you left off.
- **Read and append only the `$RUN_ID` path above** — it is the live link to your engagement memory.
- Sanctioned cyber-range only; do not record anything meant to leave the lab.
