// builtins.mjs — the harness ships with primitives, not features. Each command
// is registered exactly the way a user extension would register one, and each
// data command returns a structured value so it can be piped.

import { spawn } from 'node:child_process';
import { writeFileSync, existsSync, readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { c, glyph, rule, badge } from './theme.mjs';
import { describe, health } from './stack.mjs';

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

export function registerBuiltins(h) {
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
  h.command({
    name: 'validate',
    aliases: ['v'],
    summary: 'govern .ampersand.json spec(s) via box-and-box (pipe-able)',
    capability: 'govern.validate',
    async run(h, args, input) {
      const govern = join(h.stackRoot, 'AmpersandBoxDesign/box-and-box/bin/govern.mjs');
      if (!existsSync(govern)) return h.error('box-and-box govern CLI not found in stack');

      const targets = [];
      if (args[0]) targets.push(args[0]);
      else for (const item of asArray(input) || []) targets.push(item.file || item.name || item);
      if (!targets.length) return h.error('usage: /validate <file> | specs | validate');

      const results = [];
      for (const file of targets) {
        if (!existsSync(file)) {
          results.push({ file, verdict: 'missing', exit: 2 });
          continue;
        }
        const code = await execQuiet(h.stackRoot, 'node', [govern, file]);
        const verdict =
          { 0: 'decision', 1: 'no-admissible', 2: 'parse-error', 3: 'escalation' }[code] || 'error';
        results.push({ file, verdict, exit: code });
      }
      const data = args[0] && results.length === 1 ? results[0] : results;
      return h.present(data, () => {
        for (const r of [].concat(data)) {
          const v =
            { decision: c.green, 'no-admissible': c.amber, 'parse-error': c.red, escalation: c.violet }[
              r.verdict
            ] || c.red;
          h.out('  ' + glyph.arrow + ' ' + v(r.verdict) + c.gray('  ' + r.file));
        }
      });
    },
  });

  // ---- memory ----------------------------------------------------------
  h.command({
    name: 'memory',
    aliases: ['mem'],
    summary: 'graphonomous memory loop status',
    run(h) {
      const g = join(h.stackRoot, 'graphonomous');
      const present = existsSync(g);
      const data = { graphonomous: present, path: g, policy: 'graphonomous-first' };
      return h.present(data, () => {
        h.out('');
        h.out('  ' + c.bold('graphonomous-first memory loop'));
        h.out('  ' + rule());
        h.out('  ' + (present ? glyph.ok : glyph.idle) + ' engine ' + (present ? 'present' : 'absent') + c.gray('  ' + g));
        h.out('  ' + c.gray('loop  ') + 'retrieve ' + glyph.arrow + ' route ' + glyph.arrow + ' act ' + glyph.arrow + ' learn ' + glyph.arrow + ' consolidate');
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
