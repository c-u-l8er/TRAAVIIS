// router.mjs — the escalation router: a cost-stratified resolver that tries the
// CHEAPEST tier first and ESCALATES on a miss. Three properties make it the
// honest core of the "resolve-low / escalate-on-miss" runtime:
//
//   1. resolve-low      — tiers are walked cheapest-first; the first hit wins.
//   2. floor-gated hop  — the box-and-box floor gates the *transition* into any
//                         effectful tier (e.g. escalating to the agent/model);
//                         a refused floor refuses the hop, fail-closed.
//   3. crystallize-down — a hit from a tier that declares itself pure
//                         (`crystallize: true`) is memoized into the tier-0
//                         cache, so the next identical request resolves for free.
//
// Every resolution is appended to `router.log` — the per-tier measurement seed
// (mirrors the floor's gateLog). The router is the *mechanism*, not a policy:
// TRAAVIIS ships a two-rung ladder (deterministic capability resolution ▸
// escalate-to-agent) and extensions add rungs (κ/σ/β analyzers, `provider.*`
// model adapters) by registering resolvers. Honesty: the top `escalate` rung
// does NOT fabricate a model call — when nothing cheaper resolves, it reports an
// escalation and the harness hands the request up to the agent, which *is* the
// model (see ARCHITECTURE §"the layer boundary").

// A resolver returns MISS to decline a request (so the router escalates).
export const MISS = Symbol('router.miss');

export class Router {
  // `gate(req, label) -> { allowed, verdict }` injects the floor without a
  // module cycle; defaults to an always-allow no-floor stub for bare use.
  constructor({ gate } = {}) {
    this.tiers = []; // [{ name, cost, effectful, crystallize, try(h, req) }]
    this.memo = new Map(); // tier-0 crystallized cache: key -> value
    this.log = []; // measurement seed: one entry per route()
    this._gate = gate || (async () => ({ allowed: true, verdict: 'no-floor' }));
  }

  // Register a resolver tier. Lower cost is tried first; ties keep insertion order.
  tier(spec) {
    if (!spec || !spec.name || typeof spec.try !== 'function')
      throw new Error('router.tier requires { name, try }');
    this.tiers.push({ cost: 1, effectful: false, crystallize: false, ...spec });
    this.tiers.sort((a, b) => a.cost - b.cost);
    return this;
  }

  // Stable cache/log key for a request.
  key(req) {
    const head = req.capability ?? req.name ?? '';
    return `${head}::${JSON.stringify(req.args ?? [])}`;
  }

  // Resolve a request cheapest-first; floor-gate any effectful hop; crystallize a
  // pure hit down into the memo; record every hop. Returns a structured result.
  async route(h, req) {
    const key = this.key(req);
    const at = new Date().toISOString();
    const hops = [];

    // tier 0 — the crystallized memo: the cheapest possible resolution (cost 0).
    if (this.memo.has(key)) {
      hops.push({ tier: 'memo', cost: 0, hit: true });
      this.log.push({ at, key, resolvedBy: 'memo', cost: 0, crystallized: true, hops });
      return { key, tier: 'memo', cost: 0, value: this.memo.get(key), crystallized: true, hops };
    }

    for (const t of this.tiers) {
      // The floor gates the TRANSITION into an effectful tier (fail-closed).
      if (t.effectful) {
        const g = await this._gate(req, `route:${t.name}:${key}`);
        hops.push({ tier: t.name, gate: g.verdict, allowed: g.allowed });
        if (!g.allowed) {
          this.log.push({ at, key, resolvedBy: null, refused: true, verdict: g.verdict, cost: t.cost, hops });
          return { key, tier: null, refused: true, verdict: g.verdict, hops };
        }
      }
      const r = await t.try(h, req);
      if (r === MISS) {
        hops.push({ tier: t.name, cost: t.cost, miss: true });
        continue;
      }
      hops.push({ tier: t.name, cost: t.cost, hit: true });
      if (t.crystallize) this.memo.set(key, r); // crystallize-downward
      this.log.push({ at, key, resolvedBy: t.name, cost: t.cost, crystallized: !!t.crystallize, hops });
      return { key, tier: t.name, cost: t.cost, value: r, crystallized: !!t.crystallize, hops };
    }

    // Ladder exhausted with no resolver: fail-closed escalation marker. (With the
    // default ladder this is unreachable — the `escalate` tier always hits — but
    // it guards a custom ladder that omits a terminal tier.)
    this.log.push({ at, key, resolvedBy: null, escalated: true, hops });
    return { key, tier: null, escalate: true, hops };
  }
}
