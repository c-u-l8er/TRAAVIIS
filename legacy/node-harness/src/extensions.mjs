// extensions.mjs — the core primitive: the harness is yours to reshape.
// An extension is a .mjs file that default-exports a function receiving the
// harness context. It may register commands, themes, or hooks. Nothing here
// is a "feature" — every behavior is an extension over the same registry.
//
// Lookup order (later wins on name collision):
//   1. built-ins (src/builtins.mjs, loaded by the harness)
//   2. ~/.traaviis/extensions/*.mjs        (user-global)
//   3. <stack root>/.traaviis/extensions/*.mjs  (project-local)
//   4. paths in $TRAAVIIS_EXTENSIONS (colon-separated files or dirs)

import { readdirSync, existsSync, statSync } from 'node:fs';
import { join, basename } from 'node:path';
import { homedir } from 'node:os';
import { pathToFileURL } from 'node:url';

function collectFrom(target, out) {
  if (!existsSync(target)) return;
  let st;
  try {
    st = statSync(target);
  } catch {
    return;
  }
  if (st.isFile() && target.endsWith('.mjs')) {
    out.push(target);
  } else if (st.isDirectory()) {
    for (const f of readdirSync(target)) {
      if (f.endsWith('.mjs')) out.push(join(target, f));
    }
  }
}

export function extensionPaths(stackRoot) {
  const out = [];
  collectFrom(join(homedir(), '.traaviis', 'extensions'), out);
  collectFrom(join(stackRoot, '.traaviis', 'extensions'), out);
  for (const p of (process.env.TRAAVIIS_EXTENSIONS || '').split(':').filter(Boolean)) {
    collectFrom(p, out);
  }
  return out;
}

// Load and apply each extension against the harness context. Each extension's
// contributions (new commands + capabilities) are tracked as a plugin manifest
// so `/plugins` can report what is loaded and whether its needs are met.
export async function loadExtensions(ctx) {
  const loaded = [];
  for (const file of extensionPaths(ctx.stackRoot)) {
    const before = new Set(ctx.commands.keys());
    const capsBefore = new Set(ctx.capabilities.keys());
    try {
      const mod = await import(pathToFileURL(file).href);
      const fn = mod.default || mod.activate;
      if (typeof fn !== 'function') continue;
      await fn(ctx);
      const commands = [...ctx.commands.keys()].filter((k) => !before.has(k));
      const capabilities = [...ctx.capabilities.keys()].filter((k) => !capsBefore.has(k));
      ctx.plugins.push({
        name: mod.name || basename(file).replace(/\.mjs$/, ''),
        file,
        commands,
        capabilities,
      });
      loaded.push(file);
    } catch (err) {
      ctx.warn(`extension failed: ${file} — ${err.message}`);
    }
  }
  return loaded;
}
