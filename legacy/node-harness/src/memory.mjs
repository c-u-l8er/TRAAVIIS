// memory.mjs — the Graphonomous memory-loop adapter.
//
// TRAAVIIS is the AGENT runtime, so it (not any MCP server) drives the loop:
// retrieve → route → act → learn → consolidate. This module is the thin client
// that lets the harness reach a Graphonomous backend over MCP JSON-RPC, resolved
// FAIL-CLOSED:
//
//   GRAPHONOMOUS_MCP_URL=http://127.0.0.1:4000/mcp   → real backend (HTTP)
//   (unset / not http[s])                            → { available:false }
//
// Honesty: with no backend, recall/remember/learn report ABSENCE — they never
// fabricate memory, coverage, or outcomes. No LLM lives here; the server stores
// and returns, the agent (this harness, ultimately the model) does the reasoning.
//
// Zero-dependency: global `fetch` only. The MCP surface is the v2 machine set —
// `retrieve` / `act` / `learn` — each taking an `action` argument.

// Resolve the Graphonomous MCP endpoint (HTTP). Returns null if none configured.
export function memoryEndpoint() {
  const url = process.env.GRAPHONOMOUS_MCP_URL;
  return url && /^https?:\/\//.test(url) ? url : null;
}

const PROTOCOL_VERSION = '2025-06-18';

// One JSON-RPC round-trip over StreamableHTTP. Tolerates either an
// application/json body or a text/event-stream (SSE) body, and surfaces the
// session id the server assigns on initialize.
async function rpc(endpoint, sessionId, body, timeoutMs = 8000) {
  const headers = {
    'content-type': 'application/json',
    accept: 'application/json, text/event-stream',
  };
  if (sessionId) headers['mcp-session-id'] = sessionId;

  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  let res;
  try {
    res = await fetch(endpoint, {
      method: 'POST',
      headers,
      body: JSON.stringify(body),
      signal: ctrl.signal,
    });
  } finally {
    clearTimeout(timer);
  }

  const sid = res.headers.get('mcp-session-id') || sessionId || null;
  const ct = res.headers.get('content-type') || '';
  const text = await res.text();

  let payload = null;
  if (ct.includes('text/event-stream')) {
    const frames = text
      .split('\n')
      .filter((l) => l.startsWith('data:'))
      .map((l) => l.slice(5).trim());
    for (const f of frames.reverse()) {
      try {
        payload = JSON.parse(f);
        break;
      } catch {
        /* skip non-JSON frame */
      }
    }
  } else if (text.trim()) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = null;
    }
  }
  return { ok: res.ok, status: res.status, sessionId: sid, payload };
}

// A single MCP session: lazily handshakes (initialize → initialized) then issues
// tools/call. One client per harness so the session id is reused.
export class MemoryClient {
  constructor(endpoint = memoryEndpoint()) {
    this.endpoint = endpoint;
    this.sessionId = null;
    this._id = 0;
  }

  get available() {
    return !!this.endpoint;
  }

  async _ensureSession() {
    if (this.sessionId) return;
    const init = await rpc(this.endpoint, null, {
      jsonrpc: '2.0',
      id: ++this._id,
      method: 'initialize',
      params: {
        protocolVersion: PROTOCOL_VERSION,
        capabilities: {},
        clientInfo: { name: 'traaviis', version: '0' },
      },
    });
    this.sessionId = init.sessionId;
    // initialized notification (no id, no response expected)
    await rpc(this.endpoint, this.sessionId, {
      jsonrpc: '2.0',
      method: 'notifications/initialized',
    });
  }

  // Call one MCP machine tool. Returns a normalized, fail-closed envelope:
  //   { available:false }                         — no backend configured
  //   { available:true, ok:false, error }         — backend error / unreachable
  //   { available:true, ok:true, structured, text } — a real result
  async call(tool, args) {
    if (!this.available) return { available: false };
    try {
      await this._ensureSession();
      const r = await rpc(this.endpoint, this.sessionId, {
        jsonrpc: '2.0',
        id: ++this._id,
        method: 'tools/call',
        params: { name: tool, arguments: args },
      });
      if (!r.payload || r.payload.error) {
        return {
          available: true,
          ok: false,
          error: r.payload?.error?.message || `http ${r.status}`,
        };
      }
      const result = r.payload.result || {};
      const structured = result.structuredContent ?? null;
      const text = Array.isArray(result.content)
        ? (result.content.find((c) => c.type === 'text')?.text ?? null)
        : null;
      return { available: true, ok: !result.isError, structured, text };
    } catch (e) {
      return { available: true, ok: false, error: String(e?.message || e) };
    }
  }

  // ---- loop verbs (thin wrappers over the v2 machines) ----
  // retrieve · "what do I know?" — κ-aware ranked context for a query.
  recall(query, opts = {}) {
    return this.call('retrieve', { action: 'context', query, ...opts });
  }
  // act · store a knowledge node.
  remember(content, opts = {}) {
    return this.call('act', { action: 'store_node', content, ...opts });
  }
  // learn · close the loop with an outcome signal (status: success|partial_success|failure|timeout).
  learn(outcome = {}) {
    return this.call('learn', { action: 'from_outcome', ...outcome });
  }
}
