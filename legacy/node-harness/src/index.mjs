// index.mjs — programmatic + CLI entry for the TRAAVIIS harness.
// Four ways in: interactive REPL, print (one-shot), json, and as a library.

import { Harness } from './harness.mjs';
import { registerBuiltins } from './builtins.mjs';
import { loadExtensions } from './extensions.mjs';
import { repl } from './repl.mjs';
import { c } from './theme.mjs';

const USAGE = `${c.bold('traaviis')} — a minimal terminal harness for the [&] Protocol stack

  traaviis                      start the interactive harness
  traaviis <command> [args]     run one command and exit (print mode)
  traaviis --json <command>     run one command, emit structured json
  traaviis --help               this help

  the harness is yours: drop a .mjs in ~/.traaviis/extensions/ to add commands.
`;

// Build a fully wired harness (builtins + user/project extensions applied).
export async function createHarness(opts = {}) {
  const h = new Harness(opts);
  // expose a context surface extensions can use without importing internals
  h.warn = h.warn.bind(h);
  registerBuiltins(h);
  await loadExtensions(h);
  return h;
}

export async function main(argv = process.argv.slice(2)) {
  if (argv.includes('--help') || argv.includes('-h')) {
    process.stdout.write(USAGE);
    return 0;
  }

  const json = argv.includes('--json');
  const rest = argv.filter((a) => a !== '--json');

  // No command → interactive REPL.
  if (rest.length === 0 && !json) {
    const h = await createHarness({ mode: 'interactive' });
    await repl(h);
    return 0;
  }

  // One-shot: print or json mode. The harness exit code is returned so a
  // gating command (e.g. /validate) can fail-closed the process on a DENY.
  const h = await createHarness({ mode: json ? 'json' : 'print' });
  await h.run(rest.join(' '));
  return h.exitCode ?? 0;
}

export { Harness };
