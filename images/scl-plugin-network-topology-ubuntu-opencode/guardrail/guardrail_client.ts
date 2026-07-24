/**
 * guardrail_client.ts — thin TypeScript fetch wrapper around the OpenCode HTTP API.
 *
 * Ported from the Python OpenCodeClient pattern in
 * images/scl-plugin-network-topology-ubuntu-opencode/shared/opencode_client.py,
 * but stripped down to exactly what the guardrail plugin (guardrail.ts) needs to
 * drive the GUARDRAIL opencode serve on 127.0.0.1:4097:
 *
 *   createSession -> promptAsync -> poll /session/status -> getMessages
 *                  -> (abortSession on timeout)
 *
 * Design constraints (per GUARDRAIL_IMPLEMENTATION_PLAN.md section 5 / shared contract):
 *   - Dependency-free (uses the global fetch provided by Bun).
 *   - Guardrail is loopback-only => Authorization header is OPTIONAL (default none).
 *   - Short request timeouts; recoverable HTTP/parse failures return null rather
 *     than throw, so the plugin can apply its fail-safe (refuse + escalate).
 *     Only programming-level misuse throws a typed GuardrailClientError.
 *   - Status enums + grace-period logic mirror the Python client so behaviour
 *     (especially the "saw_busy before idle counts" race guard) is identical.
 *
 * NOTE: This module talks to the GUARDRAIL process (4097), NOT the executor (4096).
 * It is imported only by the executor-side plugin (guardrail.ts). The guardrail
 * process itself never loads the plugin, so there is no recursion here.
 */

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/** Default base URL of the GUARDRAIL opencode serve (loopback, not published). */
export const DEFAULT_GUARDRAIL_BASE = "http://127.0.0.1:4097";

/** Milliseconds between status polls inside runToIdle. */
const DEFAULT_POLL_MS = 1000;

/** Hard ceiling for a single runToIdle wait (mirrors Python DEFAULT_TIMEOUT). */
const DEFAULT_TIMEOUT_MS = 60_000;

/**
 * Grace period during which an "idle/ready" status is NOT yet treated as
 * completion. Matches shared/constants.py GRACE_PERIOD_SECONDS = 15.
 * Prevents the race where polling starts before the server picks up the
 * async prompt.
 */
const GRACE_PERIOD_MS = 15_000;

/** Per-request HTTP timeout (ms) for ordinary GET/POST calls. */
const REQUEST_TIMEOUT_MS = 30_000;

/** Health-check timeout (ms) — short, we retry. */
const HEALTH_TIMEOUT_MS = 5_000;

/** Retries used by getMessages before giving up (mirrors Python). */
const GET_MESSAGE_RETRIES = 3;

/** Statuses that mean the session is actively working. */
const BUSY_STATES = ["busy", "pending", "running", "active", "generating"];

/** Statuses that mean the session is finished (or accepted-as-finished). */
const IDLE_STATES = ["completed", "idle", "ready", "done"];

/** Errors that are always final (the session errored). */
const FINAL_ERROR_STATES = ["error", "failed"];

/** A message part as emitted by the opencode HTTP API. */
export interface MessagePart {
  type: string;
  text?: string;
  tool?: string;
  state?: Record<string, unknown>;
  [k: string]: unknown;
}

/** A message object as returned by GET /session/{id}/message. */
export interface ApiMessage {
  id?: string;
  role?: string;
  type?: string;
  info?: {
    role?: string;
    tokens?: { input?: number; output?: number; reasoning?: number };
    cost?: number;
    [k: string]: unknown;
  };
  parts?: MessagePart[];
  content?: string | MessagePart[] | unknown;
  text?: string;
  [k: string]: unknown;
}

/** Body shape for prompt_async (mirrors Python: parts + agent). */
export interface PromptBody {
  parts: { type: "text"; text: string }[];
  agent: string;
}

/** Optional request options shared by all calls. */
export interface CallOptions {
  /** Override the per-request timeout (ms). */
  timeoutMs?: number;
  /** Optional bearer token / api key. Loopback guardrail usually has none. */
  authToken?: string;
  /** Optional fetch-level AbortSignal (caller-controlled). */
  signal?: AbortSignal;
}

/** Options for runToIdle. */
export interface RunToIdleOptions extends CallOptions {
  /** Poll interval (ms). Default 1000. */
  pollMs?: number;
  /** Max wall-clock time to wait (ms). Default 60000. */
  timeoutMs?: number;
  /**
   * If true (default), call abortSession when runToIdle times out, so the
   * guardrail process does not keep a runaway session alive.
   */
  abortOnTimeout?: boolean;
}

/** Result of runToIdle. */
export interface RunToIdleResult {
  /** Final messages array, or null if it could not be fetched. */
  messages: ApiMessage[] | null;
  /** Why the wait ended. */
  reason:
    | "completed" // saw busy then idle/error
    | "idle-after-grace" // never saw busy but exceeded grace period
    | "completed-immediate" // completed on first poll (e.g. errored)
    | "timeout" // hit timeoutMs
    | "status-error" // could not read status at all
    | "messages-error"; // completed but messages fetch failed
  /** Last raw status value observed (for debugging / trace). */
  lastStatus: unknown;
  /** Elapsed wall-clock ms. */
  elapsedMs: number;
}

/** Typed error for programming misuse (bad base URL, etc.). Recoverable HTTP /
 * parse failures return null instead — the caller applies fail-safe policy. */
export class GuardrailClientError extends Error {
  cause?: unknown;
  constructor(message: string, cause?: unknown) {
    super(message);
    this.name = "GuardrailClientError";
    this.cause = cause;
  }
}

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

/** Sleep for ms. Resolves early if signal aborts. */
function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    if (ms <= 0) {
      resolve();
      return;
    }
    const t = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    const onAbort = () => {
      clearTimeout(t);
      resolve();
    };
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

/** Normalise a base URL (strip trailing slash). */
function normalizeBase(base?: string): string {
  const b = (base && base.trim()) || DEFAULT_GUARDRAIL_BASE;
  if (!/^https?:\/\//i.test(b)) {
    throw new GuardrailClientError(
      `Invalid guardrail base URL (must start with http(s)://): ${b}`,
    );
  }
  return b.replace(/\/+$/, "");
}

/** Build an AbortController that fires after timeoutMs and is linked to signal. */
function timeoutSignal(timeoutMs: number, signal?: AbortSignal): AbortSignal {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(new Error("request timeout")), timeoutMs);
  // If the caller's signal aborts, propagate.
  if (signal) {
    if (signal.aborted) ctrl.abort(signal.reason);
    else
      signal.addEventListener(
        "abort",
        () => ctrl.abort(signal.reason),
        { once: true },
      );
  }
  // Best-effort clear once this controller settles.
  ctrl.signal.addEventListener(
    "abort",
    () => clearTimeout(t),
    { once: true },
  );
  return ctrl.signal;
}

/** Headers incl. optional auth + json content-type. */
function buildHeaders(withBody: boolean, authToken?: string): HeadersInit {
  const h: Record<string, string> = { Accept: "application/json" };
  if (withBody) h["Content-Type"] = "application/json";
  if (authToken) h["Authorization"] = `${authToken}`;
  return h;
}

/** Coerce a fetch body into parsed JSON, returning null on any failure. */
async function parseJson(res: Response): Promise<any | null> {
  try {
    const txt = await res.text();
    if (!txt) return null;
    return JSON.parse(txt);
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * GET /global/health — returns true if the server reports healthy=true.
 * Never throws; returns false on any error (used in a poll loop).
 */
export async function checkHealth(
  base?: string,
  opts?: CallOptions,
): Promise<boolean> {
  const b = normalizeBase(base);
  try {
    const res = await fetch(`${b}/global/health`, {
      method: "GET",
      headers: buildHeaders(false, opts?.authToken),
      signal: timeoutSignal(HEALTH_TIMEOUT_MS, opts?.signal),
    });
    if (!res.ok) return false;
    const body = await parseJson(res);
    return Boolean(body && body.healthy === true);
  } catch {
    return false;
  }
}

/**
 * Poll /global/health until healthy or timeout (ms). Returns true on success.
 * Default timeout 120s (mirrors Python wait_for_server).
 */
export async function waitForServer(
  base?: string,
  timeoutMs: number = 120_000,
  opts?: CallOptions,
): Promise<boolean> {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    if (opts?.signal?.aborted) return false;
    if (await checkHealth(base, opts)) return true;
    await sleep(2_000, opts?.signal);
  }
  return false;
}

/**
 * POST /session — create a new session.
 * @returns the session id, or null on failure.
 */
export async function createSession(
  base?: string,
  title?: string,
  opts?: CallOptions,
): Promise<string | null> {
  const b = normalizeBase(base);
  try {
    const body: Record<string, unknown> = {};
    if (title) body.title = title;
    const res = await fetch(`${b}/session`, {
      method: "POST",
      headers: buildHeaders(true, opts?.authToken),
      body: JSON.stringify(body),
      signal: timeoutSignal(opts?.timeoutMs ?? REQUEST_TIMEOUT_MS, opts?.signal),
    });
    if (!res.ok) return null;
    const json = await parseJson(res);
    if (!json || typeof json.id !== "string") return null;
    return json.id;
  } catch {
    return null;
  }
}

/**
 * POST /session/{id}/prompt_async — fire a prompt at the guardrail agent.
 * Body mirrors the Python client: { parts: [{type:"text", text}], agent }.
 * @returns true if accepted (HTTP 200/204).
 */
export async function promptAsync(
  base: string | undefined,
  sessionId: string,
  message: string,
  agent: string,
  opts?: CallOptions,
): Promise<boolean> {
  if (!sessionId) {
    throw new GuardrailClientError("promptAsync: sessionId is required");
  }
  const b = normalizeBase(base);
  const body: PromptBody = {
    parts: [{ type: "text", text: message }],
    agent,
  };
  try {
    const res = await fetch(`${b}/session/${encodeURIComponent(sessionId)}/prompt_async`, {
      method: "POST",
      headers: buildHeaders(true, opts?.authToken),
      body: JSON.stringify(body),
      signal: timeoutSignal(opts?.timeoutMs ?? REQUEST_TIMEOUT_MS, opts?.signal),
    });
    return res.status === 200 || res.status === 204;
  } catch {
    return false;
  }
}

/**
 * Result of a synchronous guardrail prompt.
 */
export interface PromptSyncResult {
  ok: boolean;
  /** The last assistant text extracted from the response, or null. */
  text: string | null;
  /** HTTP status (diagnostic). */
  status: number;
}

/**
 * POST /session/{id}/message — SYNCHRONOUS prompt (blocks until the guardrail
 * agent finishes its turn, then returns the assistant message inline).
 *
 * This is the reliable path on opencode 1.17.9: the async path (prompt_async +
 * polling /session/status + GET /message) does NOT surface the guardrail's output
 * on a loopback serve (/session/status returns {} and /message stays empty for
 * async-prompted sessions). The sync endpoint returns the completed message in
 * the response body — either an array of messages or { info, parts:[...] }.
 *
 * @returns { ok, text } where text is the last assistant text part, best-effort.
 *          Never throws — returns { ok:false, text:null } on any failure.
 */
export async function promptSync(
  base: string | undefined,
  sessionId: string,
  message: string,
  agent: string,
  opts?: CallOptions,
): Promise<PromptSyncResult> {
  if (!sessionId) {
    throw new GuardrailClientError("promptSync: sessionId is required");
  }
  const b = normalizeBase(base);
  const body: PromptBody = {
    parts: [{ type: "text", text: message }],
    agent,
  };
  try {
    const res = await fetch(`${b}/session/${encodeURIComponent(sessionId)}/message`, {
      method: "POST",
      headers: buildHeaders(true, opts?.authToken),
      body: JSON.stringify(body),
      signal: timeoutSignal(opts?.timeoutMs ?? REQUEST_TIMEOUT_MS, opts?.signal),
    });
    if (!res.ok) {
      return { ok: false, text: null, status: res.status };
    }
    const json: unknown = await parseJson(res);
    let text: string | null = null;
    if (Array.isArray(json)) {
      text = getLastAssistantText(json as ApiMessage[]);
    } else if (json && typeof json === "object") {
      const obj = json as Record<string, unknown>;
      // opencode 1.17.9 sync shape: { info: {...}, parts: [ {type:"text",text}, ... ] }
      if (Array.isArray(obj.parts)) {
        for (const p of obj.parts as Array<Record<string, unknown>>) {
          if (p && p.type === "text" && typeof p.text === "string" && p.text.length) {
            text = p.text;
          }
        }
      } else if (Array.isArray(obj.messages)) {
        text = getLastAssistantText(obj.messages as ApiMessage[]);
      } else if (typeof obj.text === "string") {
        text = obj.text;
      }
    }
    return { ok: true, text, status: res.status };
  } catch {
    return { ok: false, text: null, status: 0 };
  }
}

/**
 * GET /session/status — returns the full status map (all sessions).
 * Never throws; returns null on failure.
 */
export async function getStatus(
  base?: string,
  opts?: CallOptions,
): Promise<Record<string, unknown> | null> {
  const b = normalizeBase(base);
  try {
    const res = await fetch(`${b}/session/status`, {
      method: "GET",
      headers: buildHeaders(false, opts?.authToken),
      signal: timeoutSignal(opts?.timeoutMs ?? 10_000, opts?.signal),
    });
    if (!res.ok) return null;
    const json = await parseJson(res);
    return json && typeof json === "object" ? (json as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}

/**
 * GET /session/status filtered to one session id.
 * @returns the status value for that session (string/object), or null if the
 *          session is absent or the request failed.
 */
export async function getSessionStatus(
  base: string | undefined,
  sessionId: string,
  opts?: CallOptions,
): Promise<unknown | null> {
  if (!sessionId) return null;
  const all = await getStatus(base, opts);
  if (!all) return null;
  return Object.prototype.hasOwnProperty.call(all, sessionId)
    ? all[sessionId]
    : null;
}

/**
 * GET /session/{id}/message — fetch all messages, with up to 3 retries.
 * Mirrors Python get_session_messages.
 * @returns array of messages, or null on failure.
 */
export async function getMessages(
  base: string | undefined,
  sessionId: string,
  opts?: CallOptions,
): Promise<ApiMessage[] | null> {
  if (!sessionId) {
    throw new GuardrailClientError("getMessages: sessionId is required");
  }
  const b = normalizeBase(base);
  for (let attempt = 1; attempt <= GET_MESSAGE_RETRIES; attempt++) {
    try {
      const res = await fetch(`${b}/session/${encodeURIComponent(sessionId)}/message`, {
        method: "GET",
        headers: buildHeaders(false, opts?.authToken),
        signal: timeoutSignal(opts?.timeoutMs ?? REQUEST_TIMEOUT_MS, opts?.signal),
      });
      if (res.ok) {
        const json = await parseJson(res);
        if (Array.isArray(json)) return json as ApiMessage[];
        // Non-array body — treat as failure but don't crash.
      }
    } catch {
      // fall through to retry
    }
    if (attempt < GET_MESSAGE_RETRIES) await sleep(2_000, opts?.signal);
  }
  return null;
}

/**
 * POST /session/{id}/abort — abort a running session.
 * @returns true if the server acknowledged (HTTP 200).
 */
export async function abortSession(
  base: string | undefined,
  sessionId: string,
  opts?: CallOptions,
): Promise<boolean> {
  if (!sessionId) return false;
  const b = normalizeBase(base);
  try {
    const res = await fetch(`${b}/session/${encodeURIComponent(sessionId)}/abort`, {
      method: "POST",
      headers: buildHeaders(false, opts?.authToken),
      signal: timeoutSignal(opts?.timeoutMs ?? 10_000, opts?.signal),
    });
    return res.status === 200;
  } catch {
    return false;
  }
}

/**
 * Poll /session/status until the guardrail session is idle/complete (or errors,
 * or times out), then fetch its messages.
 *
 * Mirrors Python wait_for_session_complete's race guard:
 *   - Track whether we ever saw a BUSY status. An IDLE status only counts as
 *     completion if we previously saw busy, OR if the grace period elapsed
 *     (so we don't mistake a not-yet-picked-up async prompt for "done").
 *   - "error"/"failed" statuses are final immediately.
 *   - Session disappearing from the status map is treated as completion once
 *     we've seen busy or passed the grace period.
 *
 * @returns RunToIdleResult. messages may be null if the messages fetch failed
 *          even though the session completed (reason: "messages-error").
 *          Never throws.
 */
export async function runToIdle(
  base: string | undefined,
  sessionId: string,
  opts?: RunToIdleOptions,
): Promise<RunToIdleResult> {
  const start = Date.now();
  const empty: RunToIdleResult = {
    messages: null,
    reason: "status-error",
    lastStatus: null,
    elapsedMs: 0,
  };
  if (!sessionId) {
    throw new GuardrailClientError("runToIdle: sessionId is required");
  }

  const pollMs = opts?.pollMs ?? DEFAULT_POLL_MS;
  const timeoutMs = opts?.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const abortOnTimeout = opts?.abortOnTimeout ?? true;

  let sawBusy = false;
  let lastStatus: unknown = null;
  let sawStatusNull = false;

  const finish = async (
    reason: RunToIdleResult["reason"],
  ): Promise<RunToIdleResult> => {
    const elapsedMs = Date.now() - start;
    let messages: ApiMessage[] | null = null;
    let finalReason = reason;
    if (reason !== "timeout") {
      messages = await getMessages(base, sessionId, opts);
      if (messages === null && reason !== "status-error") {
        finalReason = "messages-error";
      }
    }
    return { messages, reason: finalReason, lastStatus, elapsedMs };
  };

  while (true) {
    if (opts?.signal?.aborted) {
      if (abortOnTimeout) await abortSession(base, sessionId, opts);
      return { ...empty, reason: "timeout", elapsedMs: Date.now() - start };
    }
    if (Date.now() - start >= timeoutMs) {
      if (abortOnTimeout) await abortSession(base, sessionId, opts);
      return { ...empty, reason: "timeout", lastStatus, elapsedMs: Date.now() - start };
    }

    const status = await getSessionStatus(base, sessionId, opts);
    lastStatus = status;
    const elapsedMs = Date.now() - start;

    // Session absent from the status map.
    if (status === null || status === undefined) {
      sawStatusNull = true;
      // Treat as completion if we already saw busy, or we're past the grace
      // period (mirrors Python "status is None" branch).
      if (sawBusy || elapsedMs >= GRACE_PERIOD_MS) {
        return await finish("completed");
      }
      await sleep(pollMs, opts?.signal);
      continue;
    }

    const statusStr = String(
      typeof status === "object" ? JSON.stringify(status) : status,
    ).toLowerCase();

    if (BUSY_STATES.some((s) => statusStr.includes(s))) {
      sawBusy = true;
    }

    // Hard errors are always final.
    if (FINAL_ERROR_STATES.some((s) => statusStr.includes(s))) {
      return await finish("completed");
    }

    // Idle / completed states.
    if (IDLE_STATES.some((s) => statusStr.includes(s))) {
      if (sawBusy) {
        return await finish("completed");
      }
      if (elapsedMs >= GRACE_PERIOD_MS) {
        return await finish("idle-after-grace");
      }
      // First-poll idle without ever seeing busy and still inside grace —
      // keep polling; the async prompt may not have been picked up yet.
    }

    await sleep(pollMs, opts?.signal);
  }
}

// ---------------------------------------------------------------------------
// Convenience: extract the last assistant text from a messages array.
// (The guardrail plugin needs this to find the strict JSON verdict the guardrail
//  agent emits. Defensive against glm-5.2's occasional malformed tool-call text.)
// ---------------------------------------------------------------------------
export function getLastAssistantText(
  messages: ApiMessage[] | null | undefined,
): string {
  if (!messages || !Array.isArray(messages)) return "";
  for (let i = messages.length - 1; i >= 0; i--) {
    const msg = messages[i];
    if (!msg || typeof msg !== "object") continue;
    const role = (msg.role ?? msg.type ?? "") as string;
    if (role !== "assistant" && role !== "model") continue;

    let content: unknown = msg.content ?? msg.text;
    if (content === undefined && Array.isArray(msg.parts)) {
      content = msg.parts
        .map((p) => (p && typeof p === "object" && typeof p.text === "string" ? p.text : ""))
        .join(" ");
    }
    if (Array.isArray(content)) {
      content = content
        .map((p) =>
          p && typeof p === "object" && typeof (p as MessagePart).text === "string"
            ? (p as MessagePart).text
            : String(p),
        )
        .join(" ");
    } else if (typeof content !== "string") {
      content = String(content ?? "");
    }
    const text = (content as string).trim();
    if (text) return text;
  }
  return "";
}

export const __test__ = {
  BUSY_STATES,
  IDLE_STATES,
  FINAL_ERROR_STATES,
  GRACE_PERIOD_MS,
  normalizeBase,
};
