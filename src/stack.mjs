// stack.mjs — discovery of the [&] Protocol stack from the filesystem.
// The harness "harnesses" whatever stack root it is pointed at. No hard-coded
// truth: it reads sibling product directories and their markers live.

import { readdirSync, existsSync, readFileSync, statSync } from 'node:fs';
import { join, dirname, basename } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';

const HERE = dirname(fileURLToPath(import.meta.url));

// Resolve the stack root: env override → parent-of-package → cwd.
export function stackRoot() {
  if (process.env.TRAAVIIS_STACK_ROOT) return process.env.TRAAVIIS_STACK_ROOT;
  const pkgRoot = dirname(HERE); // .../TRAAVIIS
  const parent = dirname(pkgRoot); // the stack root holding all products
  if (existsSync(join(parent, 'CLAUDE.md')) || existsSync(join(parent, 'STACK_COMPLETION.md'))) {
    return parent;
  }
  return process.cwd();
}

// Resolve a box-and-box bin tool (govern.mjs, compile.mjs, …) without hard-coding
// a sibling path. Resolution order (fail-closed: returns null if none found,
// never guesses):
//   1. TRAAVIIS_KERNEL_PATH env override — a *.mjs file inside bin/ (we locate the
//      requested tool alongside it), or a package dir (we append bin/<tool>.mjs),
//      or the box-and-box package root.
//   2. an installed `box-and-box` npm package, resolved from this module.
//   3. the sibling checkout in the stack (AmpersandBoxDesign/box-and-box).
function kernelToolPath(tool, root = stackRoot()) {
  const rel = `bin/${tool}.mjs`;
  const fromDir = (d) => {
    if (!d) return null;
    // a *.mjs override points into bin/; locate the requested sibling tool there,
    // so one override resolves govern.mjs AND compile.mjs.
    if (d.endsWith('.mjs')) {
      const sib = join(dirname(d), `${tool}.mjs`);
      return existsSync(sib) ? sib : null;
    }
    const bin = join(d, rel);
    return existsSync(bin) ? bin : null;
  };

  // 1. explicit override wins.
  const env = process.env.TRAAVIIS_KERNEL_PATH;
  if (env) {
    const p = fromDir(env);
    if (p) return p;
  }

  // 2. an installed package (resolve its "." export, then locate bin/<tool>.mjs).
  try {
    const require = createRequire(import.meta.url);
    const pkgEntry = require.resolve('box-and-box'); // → .../box-and-box/index.mjs
    const p = fromDir(dirname(pkgEntry));
    if (p) return p;
  } catch {
    /* not installed — fall through */
  }

  // 3. the sibling checkout in the same stack root.
  const sibling = join(root, `AmpersandBoxDesign/box-and-box/${rel}`);
  if (existsSync(sibling)) return sibling;

  return null;
}

// The verdict CLI: judges a decision spec ({req, norms, options}).
export function kernelGovernPath(root = stackRoot()) {
  return kernelToolPath('govern', root);
}

// The govern bridge: compiles a composition [&] ampersand.json governance block
// into a box-and-box policy ({req, norms}) so it can be judged by `govern`.
export function kernelCompilePath(root = stackRoot()) {
  return kernelToolPath('compile', root);
}

function safeRead(p) {
  try {
    return readFileSync(p, 'utf8');
  } catch {
    return '';
  }
}

function isDir(p) {
  try {
    return statSync(p).isDirectory();
  } catch {
    return false;
  }
}

// Classify a single directory as a stack product (or return null).
function classify(root, name) {
  const dir = join(root, name);
  if (!isDir(dir) || name.startsWith('.') || name === 'node_modules') return null;

  const has = (rel) => existsSync(join(dir, rel));
  const markers = [];
  let kind = null;

  if (has('mix.exs')) {
    kind = 'elixir';
    markers.push('mix.exs');
  }
  if (has('package.json')) {
    kind = kind || 'node';
    markers.push('package.json');
  }
  if (has('docs/spec/README.md')) markers.push('spec');
  if (has('index.html')) markers.push('site');

  // .ampersand.json anywhere shallow → a governed [&] capability
  let governed = false;
  try {
    for (const f of readdirSync(dir)) {
      if (f.endsWith('.ampersand.json')) {
        governed = true;
        break;
      }
    }
  } catch {
    /* ignore */
  }
  if (governed) markers.push('ampersand');

  if (!markers.length) return null;

  // Pull a version if we can.
  let version = null;
  if (has('package.json')) {
    try {
      version = JSON.parse(safeRead(join(dir, 'package.json'))).version || null;
    } catch {
      /* ignore */
    }
  }

  return {
    name,
    dir,
    kind: kind || (markers.includes('site') ? 'site' : 'docs'),
    version,
    spec: markers.includes('spec'),
    governed,
    site: markers.includes('site'),
    markers,
  };
}

// One-line description harvested from a product's spec or README, if present.
export function describe(product) {
  const candidates = [
    join(product.dir, 'docs/spec/README.md'),
    join(product.dir, 'README.md'),
  ];
  for (const p of candidates) {
    const txt = safeRead(p);
    if (!txt) continue;
    for (const line of txt.split('\n')) {
      const t = line.replace(/[*_`>]/g, '').trim();
      const skip =
        !t ||
        t.startsWith('#') ||
        t.startsWith('<') ||
        t.startsWith('---') ||
        t.startsWith('!') ||
        t.startsWith('|') ||
        /^[A-Z][\w ]{0,18}:/.test(t); // "Date:", "Author:", "Last updated:" …
      if (!skip) return t.slice(0, 96);
    }
  }
  return '';
}

// Discover every product under the stack root.
export function discover(root = stackRoot()) {
  let entries = [];
  try {
    entries = readdirSync(root);
  } catch {
    return { root, products: [] };
  }
  const products = entries
    .map((n) => classify(root, n))
    .filter(Boolean)
    .sort((a, b) => a.name.localeCompare(b.name));
  return { root, products };
}

// Best-effort health signal for a product (heuristic, never blocks).
export function health(product) {
  // Governed + spec'd + versioned → "ok"; spec only → "warn"; bare → "idle".
  if (product.governed && product.spec) return 'ok';
  if (product.spec || product.version) return 'warn';
  return 'idle';
}

export { basename };
