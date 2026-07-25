// harness.mjs — the harness kernel. Five primitives, nothing more:
//   1. a command registry         (extensibility)
//   2. a pipe engine + structured  (composability)
//      values flowing between commands
//   3. capability manifests        (pluggability)
//   4. a session history tree      (replay / share)
//   5. output modes                (interactive | print | json)
// Everything a user can do is built from these. There are no "features".

import { c } from './theme.mjs';
import { stackRoot, discover } from './stack.mjs';

export class Harness {
  constructor({ mode = 'interactive' } = {}) {
    this.mode = mode; // interactive | print | json
    this.stackRoot = stackRoot();
    this.commands = new Map(); // name -> spec
    this.aliases = new Map();
    this.capabilities = new Map(); // capability -> command name
    this.plugins = []; // { name, file, commands:[], capabilities:[] }
    this.themes = new Map();
    this.hooks = { beforeRun: [], afterRun: [] };
    this.session = new Session();
    this._stackCache = null;
    this._lastStage = true; // whether the current stage's output is terminal
    this.exitCode = 0; // gating: a command may set this; one-shot mode returns it
  }

  // ---- registry (extensibility) ----------------------------------------
  // A command spec: { name, run(h, args, input) -> value, summary?, aliases?,
  //   capability?, needs?[], provides?[] }. `run` may return a structured
  //   value; that value is what the next pipe stage receives as `input`.
  command(spec) {
    if (!spec || !spec.name || typeof spec.run !== 'function') {
      throw new Error('command requires { name, run }');
    }
    this.commands.set(spec.name, spec);
    for (const a of spec.aliases || []) this.aliases.set(a, spec.name);
    if (spec.capability) this.capabilities.set(spec.capability, spec.name);
    return this;
  }

  resolve(name) {
    if (this.commands.has(name)) return this.commands.get(name);
    if (this.aliases.has(name)) return this.commands.get(this.aliases.get(name));
    if (this.capabilities.has(name)) return this.commands.get(this.capabilities.get(name));
    return null;
  }

  theme(name, palette) {
    this.themes.set(name, palette);
    return this;
  }

  hook(when, fn) {
    if (this.hooks[when]) this.hooks[when].push(fn);
    return this;
  }

  // Verify every command's declared dependencies are satisfiable.
  unmetNeeds() {
    const out = [];
    for (const cmd of this.commands.values()) {
      for (const need of cmd.needs || []) {
        if (!this.resolve(need)) out.push({ command: cmd.name, needs: need });
      }
    }
    return out;
  }

  // ---- stack access (cached) -------------------------------------------
  stack(force = false) {
    if (force || !this._stackCache) this._stackCache = discover(this.stackRoot);
    return this._stackCache;
  }

  // ---- composability ----------------------------------------------------
  // Run a command programmatically and return its structured value. Does not
  // touch the session log or terminal rendering — for use inside other
  // commands and extensions (e.g. `await h.invoke('build', [name])`).
  async invoke(name, args = [], input) {
    const cmd = this.resolve(name);
    if (!cmd) throw new Error(`unknown command: ${name}`);
    const prev = this._lastStage;
    this._lastStage = false;
    try {
      return await cmd.run(this, args, input);
    } finally {
      this._lastStage = prev;
    }
  }

  // Run a full input line: a pipeline of `cmd | cmd | cmd`. Each stage gets the
  // previous stage's return value as `input`; the final value is the result.
  async run(line) {
    const stages = parsePipeline(line);
    if (!stages.length) return;
    const node = this.session.append({ input: line, stages: stages.map((s) => s.join(' ')) });
    let input;
    for (let i = 0; i < stages.length; i++) {
      const argv = stages[i];
      const name = argv[0].replace(/^\//, '');
      const cmd = this.resolve(name);
      if (!cmd) {
        this.error(`unknown command: ${name}  (try /help)`);
        node.output = `unknown: ${name}`;
        return;
      }
      const args = argv.slice(1);
      this._lastStage = i === stages.length - 1;
      for (const h of this.hooks.beforeRun) await h(this, { name, args, stage: i });
      try {
        input = await cmd.run(this, args, input);
        node.output = input;
      } catch (err) {
        this.error(err.message);
        node.output = `error: ${err.message}`;
        return;
      }
      for (const h of this.hooks.afterRun) await h(this, { name, args, node });
    }
    return input;
  }

  // ---- output -----------------------------------------------------------
  // present(data, prettyFn): the canonical way a data command emits. It returns
  // `data` so it can be piped, but only *renders* when it is the terminal stage:
  //   - json mode  → emit JSON
  //   - otherwise  → call prettyFn() for human output
  present(data, prettyFn) {
    if (this._lastStage) {
      if (this.mode === 'json') process.stdout.write(JSON.stringify(data) + '\n');
      else if (typeof prettyFn === 'function') prettyFn();
    }
    return data;
  }

  out(s = '') {
    if (this.mode === 'json') return;
    process.stdout.write(s + '\n');
  }
  info(s) {
    this.out(c.gray('  ' + s));
  }
  warn(s) {
    if (this.mode !== 'json') process.stderr.write(c.amber('  ! ' + s) + '\n');
  }
  error(s) {
    if (this.mode !== 'json') process.stderr.write(c.red('  ✕ ' + s) + '\n');
  }
}

// A tree-structured, exportable session history (the replay/share primitive).
class Session {
  constructor() {
    this.nodes = [];
    this.startedAt = new Date().toISOString();
  }
  append(entry) {
    const node = { id: this.nodes.length, at: new Date().toISOString(), ...entry };
    this.nodes.push(node);
    return node;
  }
  toJSON() {
    // outputs may be large/structured; keep them but stringify-safe
    return {
      startedAt: this.startedAt,
      nodes: this.nodes.map((n) => ({
        id: n.id,
        at: n.at,
        input: n.input,
        stages: n.stages,
      })),
    };
  }
}

// Split a line into pipeline stages on top-level `|`, each tokenized.
export function parsePipeline(line) {
  return splitTop(line, '|')
    .map((s) => tokenize(s.trim()))
    .filter((argv) => argv.length);
}

// Split on a delimiter that is not inside quotes.
function splitTop(line, delim) {
  const out = [];
  let cur = '';
  let q = null;
  for (const ch of line) {
    if (q) {
      cur += ch;
      if (ch === q) q = null;
    } else if (ch === '"' || ch === "'") {
      q = ch;
      cur += ch;
    } else if (ch === delim) {
      out.push(cur);
      cur = '';
    } else {
      cur += ch;
    }
  }
  out.push(cur);
  return out;
}

// Minimal shell-like tokenizer: respects single + double quotes.
export function tokenize(line) {
  const out = [];
  const re = /"([^"]*)"|'([^']*)'|(\S+)/g;
  let m;
  while ((m = re.exec(line)) !== null) out.push(m[1] ?? m[2] ?? m[3]);
  return out;
}
