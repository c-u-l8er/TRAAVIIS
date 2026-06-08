// stack.mjs — discovery of the [&] Protocol stack from the filesystem.
// The harness "harnesses" whatever stack root it is pointed at. No hard-coded
// truth: it reads sibling product directories and their markers live.

import { readdirSync, existsSync, readFileSync, statSync } from 'node:fs';
import { join, dirname, basename } from 'node:path';
import { fileURLToPath } from 'node:url';

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
