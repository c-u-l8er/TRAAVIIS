// Minimal zero-dependency tests for the harness core.
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { readFileSync, rmSync } from 'node:fs';
import { createServer } from 'node:http';
import { Harness, tokenize, parsePipeline } from '../src/harness.mjs';
import { registerBuiltins, gate, firstSpec } from '../src/builtins.mjs';
import { Router, MISS } from '../src/router.mjs';
import { kernelGovernPath, kernelCompilePath } from '../src/stack.mjs';
import { main } from '../src/index.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const FIX = (f) => join(HERE, 'fixtures', f);
// fake-claude.sh is shared with the authoritative Python battery, which owns it.
// This tree reaches up to the live one rather than keeping a second copy that
// could drift; the dependency points legacy -> live, never the reverse, and it is
// one more reason this harness is not shipped in the packet.
const SHARED_FIX = (f) => join(HERE, '..', '..', '..', 'test', 'fixtures', f);
const SIBLING_GOVERN =
  '/home/travis/ProjectAmp2/AmpersandBoxDesign/box-and-box/bin/govern.mjs';
// keep env clean between tests. ASYNC-AWARE: it awaits fn before restoring the
// env, so a var read deep in an async chain (e.g. claudeCode reading
// TRAAVIIS_CLAUDE_BIN after several awaits) still sees the override. Sync callers
// must `await withEnv(...)` so a failed assertion still propagates.
const withEnv = async (key, val, fn) => {
  const prev = process.env[key];
  if (val == null) delete process.env[key];
  else process.env[key] = val;
  try {
    return await fn();
  } finally {
    if (prev == null) delete process.env[key];
    else process.env[key] = prev;
  }
};

let pass = 0;
function test(name, fn) {
  return Promise.resolve()
    .then(fn)
    .then(() => {
      pass++;
      console.log('  \u2713 ' + name);
    })
    .catch((e) => {
      console.error('  \u2717 ' + name + '\n    ' + (e.stack || e.message));
      process.exitCode = 1;
    });
}

await test('tokenize respects quotes', () => {
  assert.deepEqual(tokenize('info "box and box" x'), ['info', 'box and box', 'x']);
});

await test('parsePipeline splits on top-level | only', () => {
  const stages = parsePipeline('products | where kind=node | map name');
  assert.equal(stages.length, 3);
  assert.deepEqual(stages[1], ['where', 'kind=node']);
  // pipe inside quotes is not a split point
  assert.equal(parsePipeline('echo "a | b"').length, 1);
});

await test('builtins + capabilities register', () => {
  const h = new Harness();
  registerBuiltins(h);
  assert.ok(h.commands.has('status'));
  assert.equal(h.resolve('ls').name, 'products');
  assert.equal(h.resolve('stack.products').name, 'products'); // capability resolution
});

await test('composition: where / map / count over a piped array', async () => {
  const h = new Harness({ mode: 'json' });
  registerBuiltins(h);
  // feed a known array via a tiny source command
  h.command({ name: 'src', run: () => [
    { name: 'a', kind: 'node' },
    { name: 'b', kind: 'elixir' },
    { name: 'c', kind: 'node' },
  ] });
  const names = await h.run('src | where kind=node | map name');
  assert.deepEqual(names, ['a', 'c']);
  const n = await h.run('src | where kind=node | count');
  assert.equal(n, 2);
});

await test('invoke runs a command without touching session', async () => {
  const h = new Harness({ mode: 'print' });
  h.command({ name: 'double', run: (_h, [x]) => Number(x) * 2 });
  const r = await h.invoke('double', ['21']);
  assert.equal(r, 42);
  assert.equal(h.session.nodes.length, 0);
});

await test('each invokes a command per item', async () => {
  const h = new Harness({ mode: 'json' });
  registerBuiltins(h);
  const seen = [];
  h.command({ name: 'src', run: () => ['x', 'y'] });
  h.command({ name: 'touch', run: (_h, [name]) => { seen.push(name); return name; } });
  const out = await h.run('src | each touch');
  assert.deepEqual(seen, ['x', 'y']);
  assert.deepEqual(out, ['x', 'y']);
});

await test('stack discovery finds products', () => {
  const { products } = new Harness().stack();
  assert.ok(Array.isArray(products));
});

// ---- T6: kernel resolution (unhardcoded) -------------------------------

await test('kernelGovernPath finds the sibling checkout by default', async () => {
  await withEnv('TRAAVIIS_KERNEL_PATH', null, () => {
    const p = kernelGovernPath();
    assert.ok(p && p.endsWith('box-and-box/bin/govern.mjs'), `got ${p}`);
  });
});

await test('kernelGovernPath returns null when nothing resolves', async () => {
  await withEnv('TRAAVIIS_KERNEL_PATH', null, () => {
    assert.equal(kernelGovernPath('/tmp/no-such-stack-root-xyz'), null);
  });
});

await test('kernelGovernPath honors TRAAVIIS_KERNEL_PATH pointing at a govern.mjs file', async () => {
  await withEnv('TRAAVIIS_KERNEL_PATH', SIBLING_GOVERN, () => {
    // even with a bogus root, the explicit override wins
    assert.equal(kernelGovernPath('/tmp/no-such-stack-root-xyz'), SIBLING_GOVERN);
  });
});

await test('kernelGovernPath honors TRAAVIIS_KERNEL_PATH pointing at a package dir', async () => {
  const pkgDir = dirname(dirname(SIBLING_GOVERN)); // .../box-and-box
  await withEnv('TRAAVIIS_KERNEL_PATH', pkgDir, () => {
    assert.equal(kernelGovernPath('/tmp/no-such-stack-root-xyz'), SIBLING_GOVERN);
  });
});

await test('kernelGovernPath ignores a bogus override and falls through', async () => {
  await withEnv('TRAAVIIS_KERNEL_PATH', '/tmp/not-a-kernel', () => {
    const p = kernelGovernPath(); // default root → sibling
    assert.ok(p && p.endsWith('box-and-box/bin/govern.mjs'), `got ${p}`);
  });
});

await test('kernelCompilePath finds the sibling compile.mjs (the govern bridge)', async () => {
  await withEnv('TRAAVIIS_KERNEL_PATH', null, () => {
    const p = kernelCompilePath();
    assert.ok(p && p.endsWith('box-and-box/bin/compile.mjs'), `got ${p}`);
  });
});

await test('kernelCompilePath resolves alongside a govern.mjs override (one override, both tools)', async () => {
  await withEnv('TRAAVIIS_KERNEL_PATH', SIBLING_GOVERN, () => {
    // pointing at govern.mjs must also locate its sibling compile.mjs
    assert.ok(kernelCompilePath('/tmp/no-such-stack-root-xyz')?.endsWith('bin/compile.mjs'));
  });
});

// ---- T6: validate gates the exit code on the verdict -------------------

await test('harness exitCode defaults to 0', () => {
  assert.equal(new Harness().exitCode, 0);
});

await test('validate on a decision spec keeps exitCode 0 (gate pass)', async () => {
  const h = new Harness({ mode: 'json' });
  registerBuiltins(h);
  const r = await h.run(`validate ${FIX('decision.json')}`);
  assert.equal(r.verdict, 'decision');
  assert.equal(h.exitCode, 0);
});

await test('validate on a no-admissible spec sets exitCode 1 (gate deny)', async () => {
  const h = new Harness({ mode: 'json' });
  registerBuiltins(h);
  const r = await h.run(`validate ${FIX('no-admissible.json')}`);
  assert.equal(r.verdict, 'no-admissible');
  assert.equal(h.exitCode, 1);
});

await test('validate on a parse-error spec sets exitCode 2 (fail-closed)', async () => {
  const h = new Harness({ mode: 'json' });
  registerBuiltins(h);
  const r = await h.run(`validate ${FIX('parse-error.json')}`);
  assert.equal(r.verdict, 'parse-error');
  assert.equal(h.exitCode, 2);
});

await test('validate on a missing file sets exitCode 2 (fail-closed, no silent pass)', async () => {
  const h = new Harness({ mode: 'json' });
  registerBuiltins(h);
  const r = await h.run(`validate ${FIX('does-not-exist.json')}`);
  assert.equal(r.verdict, 'missing');
  assert.equal(h.exitCode, 2);
});

await test('validate reports the WORST verdict across multiple targets', async () => {
  const h = new Harness({ mode: 'json' });
  registerBuiltins(h);
  h.command({
    name: 'specs',
    run: () => [FIX('decision.json'), FIX('no-admissible.json')],
  });
  const r = await h.run('specs | validate');
  assert.ok(Array.isArray(r) && r.length === 2);
  assert.equal(h.exitCode, 1); // a single deny taints the whole batch
});

await test('validate fails closed (exitCode 2) when the kernel cannot be resolved', async () => {
  await withEnv('TRAAVIIS_KERNEL_PATH', '/tmp/not-a-kernel', async () => {
    const h = new Harness({ mode: 'json' });
    h.stackRoot = '/tmp/no-such-stack-root-xyz'; // force resolution to fail
    registerBuiltins(h);
    await h.run(`validate ${FIX('decision.json')}`);
    assert.equal(h.exitCode, 2);
  });
});

await test('validate with no targets fails closed (exitCode 2)', async () => {
  const h = new Harness({ mode: 'json' });
  registerBuiltins(h);
  h.command({ name: 'empty', run: () => [] });
  await h.run('empty | validate');
  assert.equal(h.exitCode, 2);
});

await test('one-shot main() returns the gated exit code on a deny', async () => {
  const code = await main(['--json', 'validate', FIX('no-admissible.json')]);
  assert.equal(code, 1);
});

await test('one-shot main() returns 0 on a clean decision', async () => {
  const code = await main(['--json', 'validate', FIX('decision.json')]);
  assert.equal(code, 0);
});

// ---- Phase A: mandatory floor gate on effectful (spawning) commands -----

const PROD_OK = { name: 'prod-ok', dir: FIX('prod-ok'), kind: 'node' };
const PROD_DENY = { name: 'prod-deny', dir: FIX('prod-deny'), kind: 'node' };
const PROD_BARE = { name: 'prod-bare', dir: FIX('prod-bare'), kind: 'node' };
// composition specs ({agent,governance}) — must route through compile → govern
const PROD_COMP_OK = { name: 'prod-comp-ok', dir: FIX('prod-comp-ok'), kind: 'node' };
const PROD_COMP_DENY = { name: 'prod-comp-deny', dir: FIX('prod-comp-deny'), kind: 'node' };
const PROD_COMP_BAD = { name: 'prod-comp-bad', dir: FIX('prod-comp-bad'), kind: 'node' };

await test('firstSpec finds a product *.ampersand.json (and null when none)', () => {
  assert.ok(firstSpec(FIX('prod-ok'))?.endsWith('app.ampersand.json'));
  assert.equal(firstSpec(FIX('prod-bare')), null);
});

await test('gate allows an effectful op when the product spec floors to a decision', async () => {
  const h = new Harness({ mode: 'json' });
  const g = await gate(h, PROD_OK, 'build:prod-ok');
  assert.equal(g.allowed, true);
  assert.equal(g.verdict, 'decision');
  assert.equal(h.exitCode, 0);
});

await test('gate refuses (fail-closed) when the product spec floors to no-admissible', async () => {
  const h = new Harness({ mode: 'json' });
  const g = await gate(h, PROD_DENY, 'build:prod-deny');
  assert.equal(g.allowed, false);
  assert.equal(g.verdict, 'no-admissible');
  assert.equal(h.exitCode, 1);
});

await test('gate routes a COMPOSITION spec through compile → govern (allow)', async () => {
  const h = new Harness({ mode: 'json' });
  const g = await gate(h, PROD_COMP_OK, 'build:prod-comp-ok');
  assert.equal(g.allowed, true);
  assert.equal(g.verdict, 'decision');
  assert.equal(g.kind, 'composition'); // dispatched via the bridge, not direct govern
  // the natural-language hard + soft rules cannot be auto-evaluated — surfaced, not passed
  assert.equal(g.requiresJudgment, 2);
  assert.equal(h.exitCode, 0);
});

await test('gate refuses a COMPOSITION spec whose hard rule vetoes the action', async () => {
  const h = new Harness({ mode: 'json' });
  // the fixture forbids exactly this action label → no admissible option
  const g = await gate(h, PROD_COMP_DENY, 'build:prod-comp-deny');
  assert.equal(g.allowed, false);
  assert.equal(g.verdict, 'no-admissible');
  assert.equal(g.kind, 'composition');
  assert.equal(h.exitCode, 1);
});

await test('gate fails closed on a spec that is neither decision nor composition', async () => {
  const h = new Harness({ mode: 'json' });
  const g = await gate(h, PROD_COMP_BAD, 'build:prod-comp-bad');
  assert.equal(g.allowed, false);
  assert.equal(g.verdict, 'parse-error');
  assert.equal(h.exitCode, 2);
});

await test('gate allows but records an ungoverned product in default mode', async () => {
  await withEnv('TRAAVIIS_ENFORCE', null, async () => {
    const h = new Harness({ mode: 'json' });
    const g = await gate(h, PROD_BARE, 'test:prod-bare');
    assert.equal(g.allowed, true);
    assert.equal(g.verdict, 'ungoverned');
    assert.equal(h.gateLog.length, 1);
    assert.equal(h.exitCode, 0);
  });
});

await test('gate refuses an ungoverned product in enforce mode (fail-closed)', async () => {
  await withEnv('TRAAVIIS_ENFORCE', '1', async () => {
    const h = new Harness({ mode: 'json' });
    const g = await gate(h, PROD_BARE, 'test:prod-bare');
    assert.equal(g.allowed, false);
    assert.equal(g.verdict, 'ungoverned');
    assert.equal(h.exitCode, 1);
  });
});

await test('build refuses a deny-spec product without spawning a process', async () => {
  const h = new Harness({ mode: 'json' });
  registerBuiltins(h);
  h._stackCache = { root: h.stackRoot, products: [PROD_DENY] }; // inject fixture product
  const r = await h.run('build prod-deny');
  assert.equal(r.refused, true);
  assert.equal(r.ok, false);
  assert.equal(r.verdict, 'no-admissible');
  assert.equal(h.exitCode, 1);
});

await test('gates command summarizes allow/refuse decisions', async () => {
  const h = new Harness({ mode: 'json' });
  registerBuiltins(h);
  await gate(h, PROD_OK, 'build:prod-ok');
  await gate(h, PROD_DENY, 'build:prod-deny');
  const data = await h.run('gates');
  assert.equal(data.count, 2);
  assert.equal(data.allow, 1);
  assert.equal(data.refuse, 1);
});

// ---- Phase B: the escalation router (resolve-low / escalate-on-miss) ----

await test('router resolves cheapest-first: a hit on the low tier never escalates', async () => {
  const seen = [];
  const r = new Router();
  r.tier({ name: 'low', cost: 1, try: () => { seen.push('low'); return { ok: true }; } });
  r.tier({ name: 'high', cost: 100, try: () => { seen.push('high'); return { ok: 'high' }; } });
  const out = await r.route({}, { capability: 'x' });
  assert.equal(out.tier, 'low');
  assert.deepEqual(out.value, { ok: true });
  assert.deepEqual(seen, ['low']); // high tier was never consulted
});

await test('router escalates on miss: a MISS falls through to the next rung', async () => {
  const r = new Router();
  r.tier({ name: 'low', cost: 1, try: () => MISS });
  r.tier({ name: 'high', cost: 100, try: () => ({ answered: 'high' }) });
  const out = await r.route({}, { capability: 'x' });
  assert.equal(out.tier, 'high');
  assert.deepEqual(out.value, { answered: 'high' });
  assert.ok(out.hops.some((h) => h.tier === 'low' && h.miss));
});

await test('router floor-gates the hop into an effectful tier (refused = fail-closed)', async () => {
  const gate = async () => ({ allowed: false, verdict: 'no-admissible' });
  const r = new Router({ gate });
  let ran = false;
  r.tier({ name: 'low', cost: 1, try: () => MISS });
  r.tier({ name: 'escalate', cost: 100, effectful: true, try: () => { ran = true; return { escalate: true }; } });
  const out = await r.route({}, { capability: 'x' });
  assert.equal(out.refused, true);
  assert.equal(out.verdict, 'no-admissible');
  assert.equal(ran, false); // the effectful tier never ran — the floor refused the hop
});

await test('router crystallizes a pure hit downward: the 2nd identical request is free', async () => {
  let calls = 0;
  const r = new Router();
  r.tier({ name: 'pure', cost: 5, crystallize: true, try: () => { calls++; return { v: 1 }; } });
  const a = await r.route({}, { capability: 'k' });
  const b = await r.route({}, { capability: 'k' });
  assert.equal(a.tier, 'pure');
  assert.equal(a.cost, 5);
  assert.equal(b.tier, 'memo'); // served from the crystallized tier-0 cache
  assert.equal(b.cost, 0);
  assert.equal(calls, 1); // the pure resolver ran exactly once
});

await test('built-in ladder: route resolves a registered capability deterministically', async () => {
  const h = new Harness({ mode: 'json' });
  registerBuiltins(h);
  const r = await h.run('route stack.status');
  assert.equal(r.tier, 'deterministic');
  assert.equal(r.escalate, false);
  assert.equal(r.refused, false);
});

await test('built-in ladder: an unknown capability escalates to the agent (default mode)', async () => {
  await withEnv('TRAAVIIS_ENFORCE', null, async () => {
    const h = new Harness({ mode: 'json' });
    registerBuiltins(h);
    const r = await h.run('route no.such.capability');
    assert.equal(r.tier, 'escalate');
    assert.equal(r.escalate, true);
    assert.equal(r.value.escalate, true);
  });
});

await test('built-in ladder: enforce mode refuses the escalation hop (fail-closed)', async () => {
  const h = new Harness({ mode: 'json' });
  h.enforce = true; // require a verdict to escalate (no async-env race)
  registerBuiltins(h);
  const r = await h.run('route no.such.capability');
  assert.equal(r.refused, true);
  assert.equal(r.escalate, false);
  assert.ok(h.exitCode >= 1); // the refused floor taints the exit code
});

await test('routes command summarizes the cost-ladder seed', async () => {
  await withEnv('TRAAVIIS_ENFORCE', null, async () => {
    const h = new Harness({ mode: 'json' });
    registerBuiltins(h);
    await h.run('route stack.status');
    await h.run('route no.such.capability');
    const data = await h.run('routes');
    assert.equal(data.count, 2);
    assert.equal(data.byTier.deterministic, 1);
  });
});

// ---- Phase B: the claude_code model provider rung ----------------------

const FAKE_CLAUDE = SHARED_FIX('fake-claude.sh');

await test('provider.claude_code returns a real result + cost (fake claude bin)', async () => {
  await withEnv('TRAAVIIS_CLAUDE_BIN', FAKE_CLAUDE, async () => {
    await withEnv('TRAAVIIS_ENFORCE', null, async () => {
      const h = new Harness({ mode: 'json' });
      registerBuiltins(h);
      const data = await h.run('ask hello there');
      assert.equal(data.tier, 'provider.claude_code');
      assert.equal(data.ok, true);
      assert.equal(data.result, 'hello from fake claude');
      assert.equal(data.cost_usd, 0.0012); // REAL cost from the CLI — not fabricated
      assert.equal(data.escalate, false);
      assert.equal(data.refused, false);
    });
  });
});

await test('ask maps a MODEL_TIER name to a claude --model alias', async () => {
  await withEnv('TRAAVIIS_CLAUDE_BIN', FAKE_CLAUDE, async () => {
    await withEnv('TRAAVIIS_ENFORCE', null, async () => {
      const h = new Harness({ mode: 'json' });
      registerBuiltins(h);
      const data = await h.run('ask --model cloud_frontier solve this');
      assert.equal(data.model, 'opus'); // local_small/large/cloud_frontier → haiku/sonnet/opus
      assert.equal(data.ok, true);
    });
  });
});

await test('ask escalates gracefully when the claude binary is absent', async () => {
  await withEnv('TRAAVIIS_CLAUDE_BIN', '/tmp/no-such-claude-binary-xyz', async () => {
    await withEnv('TRAAVIIS_ENFORCE', null, async () => {
      const h = new Harness({ mode: 'json' });
      registerBuiltins(h);
      const data = await h.run('ask anything');
      assert.equal(data.escalate, true); // provider MISS → ladder escalates honestly
      assert.equal(data.tier, 'escalate');
    });
  });
});

await test('provider.claude_code MISSes a request that carries no prompt', async () => {
  await withEnv('TRAAVIIS_ENFORCE', null, async () => {
    const h = new Harness({ mode: 'json' });
    registerBuiltins(h);
    // a plain capability route carries no prompt → the model rung declines
    const r = await h.run('route no.such.capability');
    assert.equal(r.tier, 'escalate');
    assert.ok(r.hops.some((hop) => hop.tier === 'provider.claude_code' && hop.miss));
  });
});

await test('ask is floor-gated: enforce mode refuses the model hop (no call)', async () => {
  await withEnv('TRAAVIIS_CLAUDE_BIN', FAKE_CLAUDE, async () => {
    const h = new Harness({ mode: 'json' });
    h.enforce = true; // require a verdict to make the effectful model hop
    registerBuiltins(h);
    const data = await h.run('ask hello');
    assert.equal(data.refused, true);
    assert.equal(data.ok, null); // the provider never ran
    assert.ok(h.exitCode >= 1);
  });
});

// ---- memory loop: Graphonomous MCP-over-HTTP adapter -------------------
// A hermetic, spec-conformant fake MCP server (node:http, zero-dep) so the
// retrieve/act/learn wiring is tested end-to-end without the live BEAM.
function fakeMcpServer() {
  const calls = { initialize: 0, toolCalls: [] };
  const server = createServer((req, res) => {
    let body = '';
    req.on('data', (d) => (body += d));
    req.on('end', () => {
      let msg = {};
      try {
        msg = JSON.parse(body);
      } catch {
        /* ignore */
      }
      if (msg.id == null) {
        res.writeHead(202).end(); // a notification (e.g. notifications/initialized)
        return;
      }
      const headers = { 'content-type': 'application/json' };
      let out = { result: {} };
      if (msg.method === 'initialize') {
        calls.initialize++;
        headers['mcp-session-id'] = 'fake-session-1';
        out = { result: { protocolVersion: '2025-06-18', capabilities: {}, serverInfo: { name: 'fake-graphonomous' } } };
      } else if (msg.method === 'tools/call') {
        const { name, arguments: a } = msg.params;
        calls.toolCalls.push({ name, args: a });
        if (name === 'retrieve')
          out = { result: { structuredContent: { nodes: [{ id: 'n1', content: 'recalled: ' + a.query }], topology: { routing: 'fast' } }, content: [{ type: 'text', text: 'recalled 1 node' }] } };
        else if (name === 'act')
          out = { result: { structuredContent: { status: 'stored', node_id: 'node-123' }, content: [{ type: 'text', text: 'stored node-123' }] } };
        else if (name === 'learn')
          out = { result: { structuredContent: { status: 'updated', applied: a.status }, content: [{ type: 'text', text: 'outcome ' + a.status }] } };
        else out = { result: { content: [{ type: 'text', text: 'unknown tool' }], isError: true } };
      }
      res.writeHead(200, headers);
      res.end(JSON.stringify({ jsonrpc: '2.0', id: msg.id, ...out }));
    });
  });
  return new Promise((resolve) => {
    server.listen(0, '127.0.0.1', () => {
      const { port } = server.address();
      resolve({ url: `http://127.0.0.1:${port}/mcp`, calls, close: () => new Promise((r) => server.close(r)) });
    });
  });
}

// Build a harness whose MemoryClient points at `url` (constructed under the env
// so memoryEndpoint() captures it synchronously).
async function withMemory(url, fn) {
  return withEnv('GRAPHONOMOUS_MCP_URL', url, async () => {
    const h = new Harness({ mode: 'json' });
    registerBuiltins(h);
    return fn(h);
  });
}

await test('recall drives the retrieve machine and returns real structured context', async () => {
  const srv = await fakeMcpServer();
  try {
    const data = await withMemory(srv.url, (h) => h.run('recall how does the gate work'));
    assert.equal(data.available, true);
    assert.equal(data.ok, true);
    assert.equal(data.structured.nodes[0].content, 'recalled: how does the gate work');
    assert.equal(srv.calls.toolCalls[0].name, 'retrieve');
    assert.equal(srv.calls.toolCalls[0].args.action, 'context');
  } finally {
    await srv.close();
  }
});

await test('remember stores a node via the act machine', async () => {
  const srv = await fakeMcpServer();
  try {
    const data = await withMemory(srv.url, (h) => h.run('remember the floor gates effectful commands'));
    assert.equal(data.ok, true);
    assert.equal(data.structured.node_id, 'node-123');
    const call = srv.calls.toolCalls.find((c) => c.name === 'act');
    assert.equal(call.args.action, 'store_node');
    assert.equal(call.args.source, 'traaviis');
  } finally {
    await srv.close();
  }
});

await test('learn closes the loop with a valid outcome status', async () => {
  const srv = await fakeMcpServer();
  try {
    const data = await withMemory(srv.url, (h) => h.run('learn success gate-dispatch shipped'));
    assert.equal(data.ok, true);
    assert.equal(data.structured.applied, 'success');
    const call = srv.calls.toolCalls.find((c) => c.name === 'learn');
    assert.equal(call.args.action, 'from_outcome');
    assert.equal(call.args.status, 'success');
    assert.equal(call.args.evidence, 'gate-dispatch shipped');
  } finally {
    await srv.close();
  }
});

await test('learn fails closed on an invalid status (no call made)', async () => {
  const srv = await fakeMcpServer();
  try {
    const out = await withMemory(srv.url, (h) => h.run('learn maybe'));
    assert.equal(out, undefined); // errored to stderr, returned nothing
    assert.equal(srv.calls.toolCalls.length, 0); // timeout≠failure, and 'maybe' is neither
  } finally {
    await srv.close();
  }
});

await test('the MCP session is established once and reused across calls', async () => {
  const srv = await fakeMcpServer();
  try {
    await withMemory(srv.url, async (h) => {
      await h.run('recall first');
      await h.run('recall second');
    });
    assert.equal(srv.calls.initialize, 1); // handshake once, not per call
    assert.equal(srv.calls.toolCalls.length, 2);
  } finally {
    await srv.close();
  }
});

await test('memory reports a connected backend when GRAPHONOMOUS_MCP_URL is set', async () => {
  const data = await withMemory('http://127.0.0.1:9/mcp', (h) => h.run('memory'));
  assert.equal(data.connected, true);
  assert.equal(data.backend, 'http://127.0.0.1:9/mcp');
  assert.equal(data.policy, 'graphonomous-first');
});

await test('recall fails closed with no backend (absence, never fabricated)', async () => {
  await withEnv('GRAPHONOMOUS_MCP_URL', null, async () => {
    const h = new Harness({ mode: 'json' });
    registerBuiltins(h);
    const data = await h.run('recall anything at all');
    assert.equal(data.available, false); // no backend → reports absence
    assert.equal(data.ok, undefined); // nothing claimed
  });
});

// ---- coverage for the remaining builtin commands -----------------------
// Capture stdout for commands whose contract is what they print (help) rather
// than what they return. Restores the original writer even on throw.
async function capture(fn) {
  const orig = process.stdout.write.bind(process.stdout);
  let buf = '';
  process.stdout.write = (s) => ((buf += s), true);
  try {
    await fn();
  } finally {
    process.stdout.write = orig;
  }
  return buf;
}

// A harness pre-loaded with a deterministic fixture stack, so product-facing
// commands (status/products/info) are tested without depending on the live repo.
function withFixtureStack(mode = 'json') {
  const h = new Harness({ mode });
  registerBuiltins(h);
  const fixture = {
    root: '/fixture/root',
    products: [
      { name: 'alpha', kind: 'node', version: '1.0.0', spec: true, governed: true, markers: ['package.json', 'spec'], dir: FIX('prod-ok') },
      { name: 'beta', kind: 'elixir', spec: true, governed: false, markers: ['mix.exs', 'spec'], dir: FIX('prod-bare') },
      { name: 'gamma', kind: 'node', markers: ['package.json'], dir: FIX('prod-bare') },
    ],
  };
  // override discovery so even the forced refresh in /status sees the fixture
  h.stack = () => fixture;
  return h;
}

await test('help lists every registered command', async () => {
  const h = new Harness({ mode: 'interactive' });
  registerBuiltins(h);
  const out = await capture(() => h.run('help'));
  for (const name of ['status', 'products', 'validate', 'route', 'gates', 'memory'])
    assert.ok(out.includes('/' + name), `help should mention /${name}`);
});

await test('status returns structured health counts', async () => {
  const h = withFixtureStack();
  const data = await h.run('status');
  assert.equal(data.total, 3);
  assert.equal(data.counts.ok, 1); // alpha: governed + spec
  assert.equal(data.counts.warn, 1); // beta: spec only
  assert.equal(data.counts.idle, 1); // gamma: bare
});

await test('products returns a health-annotated, pipe-able array', async () => {
  const h = withFixtureStack();
  const list = await h.run('products');
  assert.equal(list.length, 3);
  assert.equal(list[0].health, 'ok');
  // pipe-able: products | where kind=node | map name
  const names = await h.run('products | where kind=node | map name');
  assert.deepEqual(names, ['alpha', 'gamma']);
});

await test('products filters by name substring', async () => {
  const h = withFixtureStack();
  const list = await h.run('products alph');
  assert.equal(list.length, 1);
  assert.equal(list[0].name, 'alpha');
});

await test('info returns detail for a product and is null-safe on a miss', async () => {
  const h = withFixtureStack();
  const data = await h.run('info beta');
  assert.equal(data.name, 'beta');
  assert.equal(data.kind, 'elixir');
  assert.ok('health' in data && 'description' in data);
  // unknown product: no throw, no structured value (errors to stderr)
  assert.equal(await h.run('info does-not-exist'), undefined);
});

await test('specs returns a pipe-able array of {name,file}', async () => {
  const h = new Harness({ mode: 'json' });
  registerBuiltins(h);
  const specs = await h.run('specs'); // real stack scan
  assert.ok(Array.isArray(specs));
  for (const s of specs) {
    assert.ok(typeof s.name === 'string');
    assert.ok(String(s.file).endsWith('.ampersand.json'));
  }
});

await test('first takes the first N piped items (default 1)', async () => {
  const h = new Harness({ mode: 'json' });
  registerBuiltins(h);
  h.command({ name: 'src', run: () => [1, 2, 3, 4] });
  assert.deepEqual(await h.run('src | first'), [1]);
  assert.deepEqual(await h.run('src | first 2'), [1, 2]);
});

await test('json passes the piped value through unchanged and prints it', async () => {
  const h = new Harness({ mode: 'print' });
  registerBuiltins(h);
  h.command({ name: 'src', run: () => ({ a: 1, b: [2, 3] }) });
  let value;
  const out = await capture(async () => {
    value = await h.run('src | json');
  });
  assert.deepEqual(value, { a: 1, b: [2, 3] }); // pass-through (pipe-friendly)
  assert.deepEqual(JSON.parse(out), { a: 1, b: [2, 3] }); // pretty-printed payload
});

await test('memory reports the graphonomous-first loop status', async () => {
  const h = new Harness({ mode: 'json' });
  registerBuiltins(h);
  const data = await h.run('memory');
  assert.equal(data.policy, 'graphonomous-first');
  assert.equal(typeof data.graphonomous, 'boolean');
  assert.ok(String(data.path).endsWith('graphonomous'));
});

await test('plugins reports capabilities and unmet needs', async () => {
  const h = new Harness({ mode: 'json' });
  registerBuiltins(h);
  const data = await h.run('plugins');
  assert.ok(data.capabilities.includes('stack.products'));
  assert.deepEqual(data.unmet, []); // builtins declare no unsatisfiable needs
  // a command with an impossible need surfaces in unmetNeeds()
  h.command({ name: 'needy', needs: ['no.such.capability'], run: () => null });
  const after = await h.run('plugins');
  assert.ok(after.unmet.some((u) => u.command === 'needy'));
});

await test('tree returns the session history as structured nodes', async () => {
  const h = new Harness({ mode: 'json' });
  registerBuiltins(h);
  await h.run('memory');
  const data = await h.run('tree');
  assert.ok(Array.isArray(data.nodes));
  assert.ok(data.nodes.some((n) => n.input === 'memory'));
  assert.ok(typeof data.startedAt === 'string');
});

await test('export writes the session json and returns {file,nodes}', async () => {
  const h = new Harness({ mode: 'json' });
  registerBuiltins(h);
  await h.run('memory');
  const file = join(HERE, `tmp-session-${process.pid}.json`);
  try {
    const data = await h.run(`export ${file}`);
    assert.equal(data.file, file);
    assert.ok(data.nodes >= 1);
    const written = JSON.parse(readFileSync(file, 'utf8'));
    assert.ok(Array.isArray(written.nodes));
  } finally {
    rmSync(file, { force: true });
  }
});

await test('mode switches output mode and rejects invalid modes', async () => {
  const h = new Harness({ mode: 'print' });
  registerBuiltins(h);
  await h.run('mode json');
  assert.equal(h.mode, 'json');
  await h.run('mode bogus'); // invalid → unchanged (errors to stderr)
  assert.equal(h.mode, 'json');
});

await test('clear emits the ANSI screen-clear sequence', async () => {
  const h = new Harness({ mode: 'interactive' });
  registerBuiltins(h);
  const out = await capture(() => h.run('clear'));
  assert.ok(out.includes('\u001b[2J')); // clear screen + home cursor
});

await test('exit sets the harness exit flag (alias q)', async () => {
  const h = new Harness({ mode: 'interactive' });
  registerBuiltins(h);
  await h.run('q'); // alias for exit
  assert.equal(h._exit, true);
});

console.log(`\n  ${pass} passed`);
