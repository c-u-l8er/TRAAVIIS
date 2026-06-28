// builtins.mjs — the harness ships with primitives, not features. Each command
// is registered exactly the way a user extension would register one, and each
// data command returns a structured value so it can be piped.

import { spawn } from 'node:child_process';
import { writeFileSync, existsSync, readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { c, glyph, rule, badge } from './theme.mjs';
import { describe, health, kernelGovernPath, kernelCompilePath } from './stack.mjs';
import { Router, MISS } from './router.mjs';
import { MemoryClient, memoryEndpoint } from './memory.mjs';

// Run a shell command inside a dir, streaming output. Resolves to exit code.
function exec(cwd, cmd, args) {
  return new Promise((resolve) => {
    const child = spawn(cmd, args, { cwd, stdio: 'inherit', shell: false });
    child.on('error', (e) => {
      process.stderr.write(c.red(`  ✕ ${e.message}\n`));
      resolve(127);
    });
    child.on('close', (code) => resolve(code ?? 0));
  });
}

// Capture a command's exit code without inheriting stdio (for composition).
function execQuiet(cwd, cmd, args) {
  return new Promise((resolve) => {
    const child = spawn(cmd, args, { cwd, stdio: 'ignore', shell: false });
    child.on('error', () => resolve(127));
    child.on('close', (code) => resolve(code ?? 0));
  });
}

// Capture a command's exit code + stdout/stderr, optionally feeding stdin. Used
// to chain `compile | govern` in-process without a temp file (zero-dep).
function execCapture(cwd, cmd, args, stdin = null) {
  return new Promise((resolve) => {
    const child = spawn(cmd, args, { cwd, stdio: ['pipe', 'pipe', 'pipe'], shell: false });
    let out = '';
    let err = '';
    child.stdout.on('data', (d) => (out += d));
    child.stderr.on('data', (d) => (err += d));
    child.on('error', () => resolve({ code: 127, stdout: '', stderr: '' }));
    child.on('close', (code) => resolve({ code: code ?? 0, stdout: out, stderr: err }));
    child.stdin.end(stdin == null ? '' : stdin);
  });
}

function findProduct(h, name) {
  const { products } = h.stack();
  const lc = String(name).toLowerCase();
  return (
    products.find((p) => p.name.toLowerCase() === lc) ||
    products.find((p) => p.name.toLowerCase().startsWith(lc)) ||
    products.find((p) => p.name.toLowerCase().includes(lc))
  );
}

// Shallow recursive scan for *.ampersand.json specs (depth-limited).
function findSpecs(root, depth = 2) {
  const out = [];
  const walk = (dir, d) => {
    let entries;
    try {
      entries = readdirSync(dir);
    } catch {
      return;
    }
    for (const e of entries) {
      if (e === 'node_modules' || e.startsWith('.')) continue;
      const full = join(dir, e);
      let st;
      try {
        st = statSync(full);
      } catch {
        continue;
      }
      if (st.isDirectory() && d > 0) walk(full, d - 1);
      else if (e.endsWith('.ampersand.json')) out.push({ name: e, file: full });
    }
  };
  walk(root, depth);
  return out;
}

function asArray(input) {
  if (input == null) return null;
  return Array.isArray(input) ? input : [input];
}

// MODEL_TIER (PROTOCOL) name → claude --model alias; native aliases pass through.
const MODEL_ALIAS = {
  local_small: 'haiku',
  local_large: 'sonnet',
  cloud_frontier: 'opus',
  haiku: 'haiku',
  sonnet: 'sonnet',
  opus: 'opus',
};

// Real model provider: shell out to Claude Code headless (`claude -p … --output-format
// json`). With NO ANTHROPIC_API_KEY set it uses the user's subscription auth (zero
// added cost). Reports the REAL cost the CLI returns — never fabricated — and degrades
// to MISS when the binary is absent so the router escalates honestly instead of faking
// an answer. Subprocess only → keeps TRAAVIIS zero-dependency. Override the binary with
// TRAAVIIS_CLAUDE_BIN (used by the hermetic test harness).
async function claudeCode(h, prompt, req = {}) {
  const bin = process.env.TRAAVIIS_CLAUDE_BIN || 'claude';
  const alias = req.model ? MODEL_ALIAS[String(req.model).toLowerCase()] ?? String(req.model) : null;
  const args = ['-p', String(prompt), '--output-format', 'json'];
  if (alias) args.push('--model', alias);

  const res = await execCapture(h.stackRoot, bin, args);
  if (res.code === 127) return MISS; // binary not installed → let the ladder escalate
  if (res.code !== 0)
    return { provider: 'claude_code', ok: false, exit: res.code, error: (res.stderr || '').trim().slice(0, 400) };

  let parsed;
  try {
    parsed = JSON.parse(res.stdout);
  } catch {
    return { provider: 'claude_code', ok: false, error: 'unparseable --output-format json output' };
  }
  return {
    provider: 'claude_code',
    ok: true,
    model: alias || parsed.model || null,
    result: parsed.result ?? null,
    cost_usd: typeof parsed.total_cost_usd === 'number' ? parsed.total_cost_usd : null,
    session_id: parsed.session_id ?? null,
    duration_ms: typeof parsed.duration_ms === 'number' ? parsed.duration_ms : null,
  };
}

// box-and-box govern exit code → verdict label.
const VERDICTS = { 0: 'decision', 1: 'no-admissible', 2: 'parse-error', 3: 'escalation' };

// First *.ampersand.json governing spec for a product dir (depth-limited), or null.
export function firstSpec(dir) {
  const found = findSpecs(dir, 1);
  return found.length ? found[0].file : null;
}

// The candidate option fed to `govern` when gating an effectful action. A local
// toolchain action is deterministic, so beta = 1.0 reflects an ABSENCE of model
// stochasticity — NOT a fabricated confidence in any downstream outcome. The ctx
// carries only honest, mechanically-true facts about the action so compiled
// `hard`/`escalate_when` norms have something real to predicate on.
function actionOption(label) {
  const phase = String(label || '').split(':')[0] || 'action';
  return {
    id: label || 'action',
    utility: 1,
    value: { beta: 1.0, denyDefault: false },
    ctx: { action: label || 'action', phase, effectful: true },
  };
}

// Judge a single *.ampersand.json spec through box-and-box, dispatching on its
// artifact type (the SCOPE/composition-vs-decision split):
//   - decision spec  ({options:[...]})           → `govern <file>` directly
//   - composition spec ({agent,governance,...})  → `compile <file>` → inject the
//                                                  action option → pipe to `govern`
//   - neither shape                              → fail-closed parse-error
// Returns { code } on a clean verdict, or { error, exit } when the spec could not
// be governed at all (fail-closed; never a silent pass). `requiresJudgment` counts
// natural-language hard/soft rules that CANNOT be mechanically evaluated — surfaced
// honestly, never auto-passed.
async function governSpec(h, govern, spec, label) {
  let parsed;
  try {
    parsed = JSON.parse(readFileSync(spec, 'utf8'));
  } catch {
    return { error: 'parse-error', exit: 2 };
  }

  // decision spec — judge directly (preserves the original fast path)
  if (Array.isArray(parsed.options)) {
    const code = await execQuiet(h.stackRoot, 'node', [govern, spec]);
    return { code, kind: 'decision' };
  }

  // composition spec — translate governance → policy, then judge the action
  if (parsed.governance || parsed.agent) {
    const compile = kernelCompilePath(h.stackRoot);
    if (!compile) return { error: 'no-compiler', exit: 2 };
    const compiled = await execCapture(h.stackRoot, 'node', [compile, spec]);
    if (compiled.code !== 0) return { error: 'compile-error', exit: 2 };
    let policy;
    try {
      policy = JSON.parse(compiled.stdout);
    } catch {
      return { error: 'compile-error', exit: 2 };
    }
    const requiresJudgment = policy._translation?.requiresJudgment?.length || 0;
    policy.options = [actionOption(label)];
    const judged = await execCapture(h.stackRoot, 'node', [govern, '--quiet'], JSON.stringify(policy));
    return { code: judged.code, kind: 'composition', requiresJudgment };
  }

  // neither a decision nor a composition spec — cannot govern (fail-closed)
  return { error: 'parse-error', exit: 2 };
}

// The MANDATORY FLOOR for effectful (process-spawning) commands. Every command
// that spawns a process MUST call gate() first and refuse to spawn unless it
// returns { allowed: true }. This makes box-and-box governance structural, not
// advisory: the floor decides before any effect happens.
//
// Policy (fail-closed):
//   - governed product (has *.ampersand.json): box-and-box must floor to a
//     `decision` (exit 0) to permit the effect; any other verdict refuses and
//     taints h.exitCode with the verdict code.
//   - ungoverned product (no spec) or unresolvable kernel: allowed but RECORDED
//     in default mode (so the gap is measured, not hidden); REFUSED in enforce
//     mode (TRAAVIIS_ENFORCE=1 or h.enforce === true).
// Every decision is appended to h.gateLog — the seed for per-tier measurement.
export async function gate(h, product, label) {
  const enforce = h.enforce === true || process.env.TRAAVIIS_ENFORCE === '1';
  h.gateLog ||= [];
  const record = (d) => {
    h.gateLog.push({ at: new Date().toISOString(), product: product?.name ?? null, label, ...d });
    return d;
  };

  const govern = kernelGovernPath(h.stackRoot);
  const spec = product ? firstSpec(product.dir) : null;

  if (spec && govern) {
    const outcome = await governSpec(h, govern, spec, label);
    // fail-closed: a spec we could not govern at all (bad shape, missing
    // compiler, compile failure) refuses the effect and taints the exit code.
    if (outcome.error) {
      h.exitCode = Math.max(h.exitCode, outcome.exit || 2);
      return record({ allowed: false, verdict: outcome.error, governed: true, enforce, spec });
    }
    const verdict = VERDICTS[outcome.code] || 'error';
    const allowed = outcome.code === 0; // only a clean decision permits the effect
    if (!allowed) h.exitCode = Math.max(h.exitCode, outcome.code || 1);
    const extra = outcome.requiresJudgment ? { requiresJudgment: outcome.requiresJudgment } : {};
    return record({ allowed, verdict, governed: true, kind: outcome.kind, enforce, spec, ...extra });
  }

  // ungoverned (no spec) or kernel unresolved
  const verdict = govern ? 'ungoverned' : 'no-kernel';
  if (enforce) {
    h.exitCode = Math.max(h.exitCode, govern ? 1 : 2);
    return record({ allowed: false, verdict, governed: false, enforce });
  }
  return record({ allowed: true, verdict, governed: false, enforce });
}

export function registerBuiltins(h) {
  // ---- escalation router (resolve-low / escalate-on-miss) --------------
  // The cost ladder lives on the harness so extensions can add rungs. The floor
  // is injected so the router gates the hop into any effectful tier without a
  // module cycle. TRAAVIIS ships two rungs:
  //   deterministic — if a registered command/capability resolves the request,
  //                   run it (the cheap, model-free path). Resolution + a stack
  //                   command is honest "low compute"; nothing is memoized,
  //                   because command output is mutable (no stale crystals).
  //   escalate      — nothing cheaper resolved → hand the request up to the
  //                   agent (the model). Floor-gated; it does not fabricate a
  //                   model call, it reports the escalation.
  h.router = new Router({ gate: (req, label) => gate(h, req.product ?? null, label) });
  // The Graphonomous memory client (one per harness so the MCP session is reused).
  // Resolves a backend fail-closed; with none configured it reports absence and
  // never fabricates memory. See src/memory.mjs.
  h.memory = new MemoryClient();

  h.router.tier({
    name: 'deterministic',
    cost: 1,
    async try(h, req) {
      const name = String(req.capability ?? req.name ?? '').replace(/^\//, '');
      if (!name || !h.resolve(name)) return MISS;
      return await h.invoke(name, req.args ?? [], req.input);
    },
  });
  //   provider.claude_code — a REAL model rung between cheap resolution and the
  //                   terminal escalation. Only requests carrying a `prompt`
  //                   reach it (otherwise it MISSes and the ladder escalates).
  //                   Effectful: the floor gates the hop before any model call,
  //                   and the cost reported is the CLI's real total_cost_usd.
  h.router.tier({
    name: 'provider.claude_code',
    cost: 50,
    effectful: true,
    async try(h, req) {
      if (!req.prompt) return MISS; // not a model request — let escalate handle it
      return await claudeCode(h, req.prompt, req);
    },
  });
  h.router.tier({
    name: 'escalate',
    cost: 100,
    effectful: true, // the floor gates the transition to the expensive tier
    async try(_h, req) {
      return {
        escalate: true,
        request: req.capability ?? req.name ?? null,
        reason: 'no deterministic tier resolved this — hand up to the agent',
      };
    },
  });

  // ---- help ------------------------------------------------------------
  h.command({
    name: 'help',
    aliases: ['?', 'h'],
    summary: 'list commands',
    run(h) {
      h.out(c.bold('  commands') + c.gray('  — pipe them with |, override them by name'));
      h.out('');
      const rows = [...h.commands.values()].sort((a, b) => a.name.localeCompare(b.name));
      for (const cmd of rows) {
        const al = cmd.aliases?.length ? c.gray(`  (${cmd.aliases.join(', ')})`) : '';
        h.out('  ' + c.amber('/' + cmd.name.padEnd(10)) + ' ' + cmd.summary + al);
      }
      h.out('');
      h.info('compose: products | where kind=node | map name | each test');
      h.info('extend:  drop a .mjs in ~/.traaviis/extensions/');
    },
  });

  // ---- status ----------------------------------------------------------
  h.command({
    name: 'status',
    aliases: ['st'],
    summary: 'stack health overview',
    capability: 'stack.status',
    run(h) {
      const { root, products } = h.stack(true);
      const counts = { ok: 0, warn: 0, idle: 0 };
      for (const p of products) counts[health(p)]++;
      const data = { root, total: products.length, counts };
      return h.present(data, () => {
        h.out('');
        h.out('  ' + c.bold('[&] stack') + c.gray('  ' + root));
        h.out('  ' + rule());
        h.out(
          '  ' +
            `${glyph.ok} ${counts.ok} governed   ` +
            `${glyph.warn} ${counts.warn} spec'd   ` +
            `${glyph.idle} ${counts.idle} bare   ` +
            c.gray(`(${products.length} total)`)
        );
        h.out('');
      });
    },
  });

  // ---- products (returns a structured array → pipe-able) ---------------
  h.command({
    name: 'products',
    aliases: ['ls', 'p'],
    summary: 'list discovered stack products (pipe-able)',
    capability: 'stack.products',
    run(h, args) {
      const { products } = h.stack();
      const filter = args[0]?.toLowerCase();
      const list = (filter ? products.filter((p) => p.name.toLowerCase().includes(filter)) : products).map(
        (p) => ({ ...p, health: health(p) })
      );
      return h.present(list, () => {
        h.out('');
        for (const p of list) {
          const dot = { ok: glyph.ok, warn: glyph.warn, idle: glyph.idle }[p.health];
          const ver = p.version ? c.gray('v' + p.version) : '';
          h.out('  ' + dot + ' ' + c.bold(p.name.padEnd(24)) + ' ' + c.teal(p.kind.padEnd(7)) + ' ' + ver);
          h.out('    ' + p.markers.map((m) => c.gray(m)).join(c.gray(' · ')));
        }
        h.out('');
        h.info(`${list.length} product(s).  pipe with | where, | map, | each`);
      });
    },
  });

  // ---- info ------------------------------------------------------------
  h.command({
    name: 'info',
    aliases: ['show'],
    summary: 'detail for one product',
    run(h, args, input) {
      const name = args[0] || (input && (input.name || input));
      if (!name) return h.error('usage: /info <product>');
      const p = findProduct(h, name);
      if (!p) return h.error(`no product matching "${name}"`);
      const data = { ...p, health: health(p), description: describe(p) };
      return h.present(data, () => {
        h.out('');
        h.out('  ' + c.bold(p.name) + '  ' + badge(data.health, data.health));
        h.out('  ' + rule());
        h.out('  ' + c.gray('kind     ') + p.kind);
        if (p.version) h.out('  ' + c.gray('version  ') + p.version);
        h.out('  ' + c.gray('markers  ') + p.markers.join(', '));
        h.out('  ' + c.gray('path     ') + p.dir);
        if (data.description) h.out('  ' + c.gray('about    ') + data.description);
        h.out('');
      });
    },
  });

  // ---- specs (pipe-able list of [&] specs) -----------------------------
  h.command({
    name: 'specs',
    summary: 'list .ampersand.json specs in the stack (pipe-able)',
    capability: 'stack.specs',
    run(h) {
      const specs = findSpecs(h.stackRoot);
      return h.present(specs, () => {
        h.out('');
        for (const s of specs) h.out('  ' + glyph.bullet + ' ' + s.name + c.gray('  ' + s.file));
        h.out('');
        h.info(`${specs.length} spec(s).  pipe with | validate`);
      });
    },
  });

  // ---- composition primitives -----------------------------------------
  // where: filter an input array.  forms: `field` (truthy) | `field=v` | `field!=v`
  h.command({
    name: 'where',
    summary: 'filter a piped array (field | field=val | field!=val)',
    run(h, args, input) {
      const arr = asArray(input) || [];
      const out = arr.filter((item) => args.every((pred) => matchPred(item, pred)));
      return h.present(out, () => out.forEach((x) => h.out('  ' + summarize(x))));
    },
  });

  // map: project a field from each item.
  h.command({
    name: 'map',
    summary: 'project one field from each piped item',
    run(h, args, input) {
      const field = args[0];
      const arr = asArray(input) || [];
      const out = field ? arr.map((x) => (x == null ? x : x[field])) : arr;
      return h.present(out, () => out.forEach((x) => h.out('  ' + summarize(x))));
    },
  });

  // each: invoke a command once per piped item (item.name as arg, item as input).
  h.command({
    name: 'each',
    summary: 'run a command for each piped item',
    async run(h, args, input) {
      const cmd = args[0];
      if (!cmd) return h.error('usage: ... | each <command> [args]');
      const arr = asArray(input) || [];
      const out = [];
      for (const item of arr) {
        const itemArgs = [typeof item === 'string' ? item : item?.name ?? '', ...args.slice(1)];
        out.push(await h.invoke(cmd, itemArgs, item));
      }
      return out;
    },
  });

  h.command({
    name: 'first',
    summary: 'take the first N piped items (default 1)',
    run(h, args, input) {
      const n = parseInt(args[0] || '1', 10);
      const out = (asArray(input) || []).slice(0, n);
      return h.present(out, () => out.forEach((x) => h.out('  ' + summarize(x))));
    },
  });

  h.command({
    name: 'count',
    summary: 'count piped items',
    run(h, args, input) {
      const n = (asArray(input) || []).length;
      return h.present(n, () => h.out('  ' + n));
    },
  });

  h.command({
    name: 'json',
    summary: 'pretty-print the piped value as JSON',
    run(h, args, input) {
      h.out(JSON.stringify(input ?? null, null, 2));
      return input;
    },
  });

  // ---- build / test (delegate to each product's own toolchain) --------
  const toolchain = (p, phase) => {
    if (p.kind === 'elixir')
      return phase === 'test' ? ['mix', ['test']] : ['mix', ['compile', '--warnings-as-errors']];
    if (p.kind === 'node') {
      const pkg = JSON.parse(readFileSync(join(p.dir, 'package.json'), 'utf8'));
      const script = (pkg.scripts || {})[phase];
      if (script) return ['npm', ['run', phase]];
      if (phase === 'build') return ['npm', ['install']];
    }
    return null;
  };

  for (const phase of ['build', 'test']) {
    h.command({
      name: phase,
      summary: `run ${phase} for a product (delegates to its toolchain)`,
      capability: `stack.${phase}`,
      async run(h, args, input) {
        const name = args[0] || (input && (input.name || input));
        if (!name) return h.error(`usage: /${phase} <product>`);
        const p = findProduct(h, name);
        if (!p) return h.error(`no product matching "${name}"`);

        // Mandatory floor: an effectful command cannot spawn without a verdict.
        const g = await gate(h, p, `${phase}:${p.name}`);
        if (!g.allowed) {
          if (h._lastStage)
            h.error(`refused: ${phase} ${p.name} — floor verdict ${c.amber(g.verdict)} (no process spawned)`);
          return { product: p.name, phase, ok: false, refused: true, verdict: g.verdict };
        }
        if (g.verdict !== 'decision' && h._lastStage)
          h.warn(`${phase} ${p.name} ran ungoverned (${g.verdict}) — set TRAAVIIS_ENFORCE=1 to require a verdict`);

        const tc = toolchain(p, phase);
        if (!tc) {
          h.error(`no ${phase} toolchain for ${p.name} (${p.kind})`);
          return { product: p.name, phase, ok: false, skipped: true };
        }
        // quiet when piped (not the terminal stage), streamed otherwise
        const runner = h._lastStage ? exec : execQuiet;
        if (h._lastStage) h.out(c.gray(`  ▸ ${tc[0]} ${tc[1].join(' ')}  ${c.gray('in ' + p.name)}`));
        const code = await runner(p.dir, tc[0], tc[1]);
        if (h._lastStage)
          h.out(code === 0 ? c.green(`  ✓ ${phase} ok`) : c.red(`  ✕ ${phase} exited ${code}`));
        return { product: p.name, phase, ok: code === 0, exit: code };
      },
    });
  }

  // ---- validate ([&] governance; handles single file OR a piped array) -
  // Resolves the box-and-box govern CLI without a hard-coded path (env override
  // → installed package → sibling checkout), and GATES: the harness exit code
  // mirrors the kernel verdict (0 decision · 1 no-admissible · 2 parse-error ·
  // 3 escalation), so CI can fail-closed on a DENY. Fail-closed: a missing
  // kernel or missing file is a non-zero verdict, never a silent pass.
  h.command({
    name: 'validate',
    aliases: ['v'],
    summary: 'govern .ampersand.json spec(s) via box-and-box (pipe-able, gates exit code)',
    capability: 'govern.validate',
    async run(h, args, input) {
      const govern = kernelGovernPath(h.stackRoot);
      if (!govern) {
        h.exitCode = 2;
        return h.error(
          'box-and-box govern CLI not found (set TRAAVIIS_KERNEL_PATH, install box-and-box, or run inside the stack)',
        );
      }

      const targets = [];
      if (args[0]) targets.push(args[0]);
      else for (const item of asArray(input) || []) targets.push(item.file || item.name || item);
      if (!targets.length) {
        h.exitCode = 2;
        return h.error('usage: /validate <file> | specs | validate');
      }

      const results = [];
      for (const file of targets) {
        if (!existsSync(file)) {
          results.push({ file, verdict: 'missing', exit: 2 });
          continue;
        }
        const code = await execQuiet(h.stackRoot, 'node', [govern, file]);
        const verdict =
          { 0: 'decision', 1: 'no-admissible', 2: 'parse-error', 3: 'escalation' }[code] ||
          'error';
        results.push({ file, verdict, exit: code });
      }
      // Gate: the harness exit code is the worst (max) verdict exit across targets.
      h.exitCode = results.reduce((worst, r) => Math.max(worst, r.exit || 0), h.exitCode);
      const data = args[0] && results.length === 1 ? results[0] : results;
      return h.present(data, () => {
        for (const r of [].concat(data)) {
          const v =
            {
              decision: c.green,
              'no-admissible': c.amber,
              'parse-error': c.red,
              escalation: c.violet,
              missing: c.red,
            }[r.verdict] || c.red;
          h.out('  ' + glyph.arrow + ' ' + v(r.verdict) + c.gray('  ' + r.file));
        }
      });
    },
  });

  // ---- memory ----------------------------------------------------------
  h.command({
    name: 'memory',
    aliases: ['mem'],
    summary: 'graphonomous memory loop status (engine + live backend)',
    run(h) {
      const g = join(h.stackRoot, 'graphonomous');
      const present = existsSync(g);
      const endpoint = memoryEndpoint();
      const data = {
        graphonomous: present,
        path: g,
        policy: 'graphonomous-first',
        backend: endpoint, // null when no live MCP endpoint is configured
        connected: !!endpoint,
      };
      return h.present(data, () => {
        h.out('');
        h.out('  ' + c.bold('graphonomous-first memory loop'));
        h.out('  ' + rule());
        h.out('  ' + (present ? glyph.ok : glyph.idle) + ' engine ' + (present ? 'present' : 'absent') + c.gray('  ' + g));
        h.out(
          '  ' + (endpoint ? glyph.ok : glyph.idle) + ' backend ' +
            (endpoint ? c.teal(endpoint) : c.gray('not configured — set GRAPHONOMOUS_MCP_URL')),
        );
        h.out('  ' + c.gray('loop  ') + 'retrieve ' + glyph.arrow + ' route ' + glyph.arrow + ' act ' + glyph.arrow + ' learn ' + glyph.arrow + ' consolidate');
        if (!endpoint) h.info('recall/remember/learn report absence until a backend is wired (fail-closed)');
        h.out('');
      });
    },
  });

  // ---- recall (retrieve · "what do I know?") ---------------------------
  // Drives the Graphonomous `retrieve` machine. Fail-closed: with no backend it
  // reports absence rather than inventing context.
  h.command({
    name: 'recall',
    aliases: ['retrieve'],
    summary: 'retrieve κ-aware context from graphonomous (memory loop · retrieve)',
    capability: 'memory.recall',
    async run(h, args, input) {
      const query = (args.join(' ').trim() || (typeof input === 'string' ? input : '')).trim();
      if (!query) return h.error('usage: /recall <query...>');
      const r = await h.memory.recall(query);
      const data = { query, ...r };
      return h.present(data, () => memoryView(h, 'recall', query, r));
    },
  });

  // ---- remember (act · store_node) ------------------------------------
  h.command({
    name: 'remember',
    summary: 'store a knowledge node in graphonomous (memory loop · act)',
    capability: 'memory.remember',
    async run(h, args, input) {
      const content = (args.join(' ').trim() || (typeof input === 'string' ? input : '')).trim();
      if (!content) return h.error('usage: /remember <text...>');
      const r = await h.memory.remember(content, { source: 'traaviis' });
      const data = { content, ...r };
      return h.present(data, () => memoryView(h, 'remember', content, r));
    },
  });

  // ---- learn (learn · from_outcome) -----------------------------------
  // status MUST be one of success | partial_success | failure | timeout
  // (timeout is NOT failure). Fail-closed on an invalid status.
  h.command({
    name: 'learn',
    summary: 'close the loop with an outcome signal (memory loop · learn)',
    capability: 'memory.learn',
    async run(h, args) {
      const status = (args[0] || '').toLowerCase();
      const valid = ['success', 'partial_success', 'failure', 'timeout'];
      if (!valid.includes(status))
        return h.error(`usage: /learn <${valid.join('|')}> [evidence...]`);
      const evidence = args.slice(1).join(' ').trim() || null;
      const r = await h.memory.learn({ status, ...(evidence ? { evidence } : {}) });
      const data = { status, evidence, ...r };
      return h.present(data, () => memoryView(h, 'learn', status, r));
    },
  });

  // ---- gates (the floor's audit trail; measurement seed) ---------------
  h.command({
    name: 'gates',
    summary: 'show floor-gate decisions for effectful commands this session',
    capability: 'govern.gates',
    run(h) {
      const log = h.gateLog || [];
      const counts = log.reduce(
        (a, e) => ((a[e.allowed ? 'allow' : 'refuse']++, a)),
        { allow: 0, refuse: 0 },
      );
      const data = { count: log.length, ...counts, decisions: log };
      return h.present(data, () => {
        h.out('');
        h.out('  ' + c.bold('floor gates') + c.gray(`  ${log.length} decision(s)`));
        h.out('  ' + rule());
        for (const e of log) {
          const mark = e.allowed ? (e.verdict === 'decision' ? glyph.ok : glyph.warn) : glyph.idle;
          h.out(
            '  ' + mark + ' ' + c.bold(String(e.label || '').padEnd(22)) + ' ' +
              (e.allowed ? c.green('allow ') : c.red('refuse')) + c.gray('  ' + e.verdict),
          );
        }
        if (!log.length) h.info('no effectful commands gated yet — run /build or /test');
        else h.info(`${counts.allow} allowed · ${counts.refuse} refused`);
        h.out('');
      });
    },
  });

  // ---- route (resolve-low / escalate-on-miss over the cost ladder) -----
  h.command({
    name: 'route',
    summary: 'resolve a request cheapest-first; escalate-on-miss (floor-gated)',
    capability: 'router.route',
    async run(h, args, input) {
      const cap = args[0];
      if (!cap) return h.error('usage: /route <capability> [args...]');
      const req = { capability: cap, args: args.slice(1), input };
      const r = await h.router.route(h, req);
      const escalate = r.tier === 'escalate' || !!r.escalate;
      const data = {
        request: cap,
        tier: r.tier,
        cost: r.cost ?? null,
        crystallized: !!r.crystallized,
        refused: !!r.refused,
        escalate,
        verdict: r.verdict ?? null,
        value: r.value ?? null,
        hops: r.hops,
      };
      return h.present(data, () => {
        h.out('');
        h.out('  ' + c.bold('route') + c.gray('  ' + cap));
        h.out('  ' + rule());
        for (const hop of r.hops) {
          const tag = hop.hit
            ? c.green('hit')
            : hop.miss
              ? c.gray('miss')
              : hop.gate
                ? (hop.allowed ? c.violet : c.red)('floor ' + hop.gate)
                : '';
          const cost = hop.cost != null ? c.gray('cost ' + hop.cost + '  ') : '';
          h.out('  ' + glyph.arrow + ' ' + String(hop.tier).padEnd(14) + ' ' + cost + tag);
        }
        if (data.refused) h.error(`refused at the floor — verdict ${data.verdict} (no escalation)`);
        else if (escalate) h.warn('escalated to the agent — no deterministic tier resolved this');
        else
          h.out(
            '  ' + c.green(`✓ resolved by ${data.tier}`) + c.gray(`  cost ${data.cost}`) +
              (data.crystallized ? c.gray('  (memoized)') : ''),
          );
        h.out('');
      });
    },
  });

  // ---- ask (route a model request through the cost ladder; floor-gated) -
  // `/ask <prompt>` carries a prompt into the router. The deterministic rung
  // MISSes (no command resolves the routing label), the provider.claude_code rung
  // runs the real model (floor-gated, real cost), and if `claude` is absent the
  // ladder escalates honestly. `--model` accepts a MODEL_TIER name or a claude alias.
  h.command({
    name: 'ask',
    summary: 'ask the model provider via Claude Code (routes the ladder; floor-gated)',
    capability: 'provider.ask',
    async run(h, args, input) {
      let model = null;
      const words = [];
      for (let i = 0; i < args.length; i++) {
        if (args[i] === '--model' || args[i] === '-m') {
          model = args[++i] ?? null;
          continue;
        }
        words.push(args[i]);
      }
      const prompt = (words.join(' ').trim() || (typeof input === 'string' ? input : '')).trim();
      if (!prompt) return h.error('usage: /ask [--model haiku|sonnet|opus] <prompt...>');

      // 'model.complete' is a routing label no command resolves → deterministic
      // MISSes and the request reaches the provider rung, then escalate.
      const req = { capability: 'model.complete', prompt, model, input };
      const r = await h.router.route(h, req);
      const v = r.value || {};
      const escalate = r.tier === 'escalate' || !!r.escalate;
      const data = {
        prompt,
        model: v.model ?? model ?? null,
        tier: r.tier,
        provider: v.provider ?? null,
        ok: v.ok ?? null,
        result: v.result ?? null,
        cost_usd: v.cost_usd ?? null,
        session_id: v.session_id ?? null,
        refused: !!r.refused,
        verdict: r.verdict ?? null,
        escalate,
        hops: r.hops,
      };
      return h.present(data, () => {
        h.out('');
        h.out('  ' + c.bold('ask') + c.gray('  ' + (data.model || 'default')));
        h.out('  ' + rule());
        if (data.refused) h.error(`refused at the floor — verdict ${data.verdict} (no model call)`);
        else if (escalate)
          h.warn('no model provider resolved — escalated to the agent (is `claude` installed?)');
        else if (data.ok) {
          h.out(data.result || c.gray('(empty result)'));
          h.out('');
          h.out(
            '  ' +
              c.gray(`provider ${data.provider}  ·  cost ${data.cost_usd == null ? 'n/a' : '$' + data.cost_usd}`),
          );
        } else h.error(`provider error — ${v.error || 'unknown'}`);
        h.out('');
      });
    },
  });

  // ---- routes (the router's audit trail; cost-ladder measurement seed) -
  h.command({
    name: 'routes',
    summary: 'show router tier resolutions this session (cost-ladder seed)',
    capability: 'router.log',
    run(h) {
      const log = h.router?.log ?? [];
      const byTier = {};
      let cost = 0;
      for (const e of log) {
        const t = e.resolvedBy ?? (e.refused ? 'refused' : e.escalated ? 'escalate' : 'none');
        byTier[t] = (byTier[t] || 0) + 1;
        cost += e.cost || 0;
      }
      const data = { count: log.length, totalCost: cost, byTier, log };
      return h.present(data, () => {
        h.out('');
        h.out('  ' + c.bold('router') + c.gray(`  ${log.length} resolution(s) · total cost ${cost}`));
        h.out('  ' + rule());
        for (const [t, n] of Object.entries(byTier))
          h.out('  ' + glyph.bullet + ' ' + String(t).padEnd(14) + ' ' + c.gray(n + '×'));
        if (!log.length) h.info('nothing routed yet — try /route stack.status');
        h.out('');
      });
    },
  });

  // ---- plugins (pluggability surface) ----------------------------------
  h.command({
    name: 'plugins',
    summary: 'list loaded extensions, capabilities, and unmet needs',
    run(h) {
      const data = {
        plugins: h.plugins,
        capabilities: [...h.capabilities.keys()],
        themes: [...h.themes.keys()],
        unmet: h.unmetNeeds(),
      };
      return h.present(data, () => {
        h.out('');
        h.out('  ' + c.bold('plugins') + c.gray(`  ${h.plugins.length} loaded`));
        h.out('  ' + rule());
        for (const p of h.plugins) {
          h.out('  ' + glyph.ok + ' ' + c.bold(p.name) + c.gray('  ' + p.file));
          if (p.commands.length) h.out('    ' + c.gray('commands  ') + p.commands.join(', '));
          if (p.capabilities.length) h.out('    ' + c.gray('provides  ') + p.capabilities.join(', '));
        }
        if (!h.plugins.length) h.info('no extensions loaded — drop a .mjs in ~/.traaviis/extensions/');
        h.out('');
        h.out('  ' + c.gray('capabilities  ') + ([...h.capabilities.keys()].join(', ') || '—'));
        if (data.unmet.length)
          h.out('  ' + c.amber('unmet needs   ') + data.unmet.map((u) => `${u.command}→${u.needs}`).join(', '));
        h.out('');
      });
    },
  });

  // ---- session: tree / export -----------------------------------------
  h.command({
    name: 'tree',
    summary: 'show this session as a tree',
    run(h) {
      const data = h.session.toJSON();
      return h.present(data, () => {
        h.out('');
        h.out('  ' + c.bold('session') + c.gray('  ' + h.session.startedAt));
        for (const n of data.nodes) h.out('  ' + c.gray(String(n.id).padStart(3)) + ' ' + glyph.arrow + ' ' + (n.input || ''));
        h.out('');
      });
    },
  });

  h.command({
    name: 'export',
    summary: 'write the session history to a json file',
    run(h, args) {
      const out = args[0] || 'traaviis-session.json';
      writeFileSync(out, JSON.stringify(h.session.toJSON(), null, 2));
      h.out(c.green(`  ✓ exported ${h.session.nodes.length} node(s) → ${out}`));
      return { file: out, nodes: h.session.nodes.length };
    },
  });

  // ---- mode / clear / exit --------------------------------------------
  h.command({
    name: 'mode',
    summary: 'show or switch output mode (interactive|print|json)',
    run(h, args) {
      if (!args[0]) return h.out('  ' + c.gray('mode: ') + h.mode);
      if (!['interactive', 'print', 'json'].includes(args[0]))
        return h.error('mode must be interactive | print | json');
      h.mode = args[0];
      h.out(c.green(`  ✓ mode → ${h.mode}`));
    },
  });

  h.command({
    name: 'clear',
    aliases: ['cls'],
    summary: 'clear the screen',
    run() {
      process.stdout.write('\u001b[2J\u001b[H');
    },
  });

  h.command({
    name: 'exit',
    aliases: ['quit', 'q'],
    summary: 'leave the harness',
    run(h) {
      h._exit = true;
    },
  });

  return h;
}

// Shared pretty-printer for the memory-loop commands. Renders the fail-closed
// envelope honestly: absent backend, backend error, or a real result.
function memoryView(h, verb, subject, r) {
  h.out('');
  h.out('  ' + c.bold('memory ' + verb) + c.gray('  ' + String(subject).slice(0, 64)));
  h.out('  ' + rule());
  if (!r.available) {
    h.out('  ' + glyph.idle + ' ' + c.gray('no backend — set GRAPHONOMOUS_MCP_URL (nothing fabricated)'));
  } else if (!r.ok) {
    h.error(`backend error — ${r.error || 'unknown'}`);
  } else {
    h.out('  ' + glyph.ok + ' ' + c.green('ok'));
    if (r.text) h.out('  ' + c.gray(String(r.text).slice(0, 400)));
    if (r.structured) h.out('  ' + c.gray(JSON.stringify(r.structured).slice(0, 400)));
  }
  h.out('');
}

// ---- helpers for composition -------------------------------------------
function matchPred(item, pred) {
  if (item == null) return false;
  let m;
  if ((m = pred.match(/^([\w.]+)!=(.*)$/))) return String(item[m[1]]) !== m[2];
  if ((m = pred.match(/^([\w.]+)=(.*)$/))) return String(item[m[1]]) === m[2];
  return !!item[pred]; // bare field → truthy
}

function summarize(x) {
  if (x == null) return String(x);
  if (typeof x !== 'object') return String(x);
  if (x.name) return x.name + (x.kind ? c.gray('  ' + x.kind) : '');
  if (x.file) return (x.verdict ? x.verdict + '  ' : '') + x.file;
  return JSON.stringify(x);
}
