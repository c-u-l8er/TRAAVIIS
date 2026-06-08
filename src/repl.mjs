// repl.mjs — the interactive harness loop. Readline-based, no dependencies.

import readline from 'node:readline';
import { c, glyph, banner } from './theme.mjs';

export async function repl(h) {
  const rl = readline.createInterface({
    input: process.stdin,
    output: process.stdout,
    completer: (line) => {
      const names = [...h.commands.keys()].map((n) => '/' + n);
      const hits = names.filter((n) => n.startsWith(line));
      return [hits.length ? hits : names, line];
    },
  });

  if (process.stdout.isTTY) {
    process.stdout.write('\n' + banner() + '\n\n');
    const { products } = h.stack();
    h.info(`harnessing ${products.length} products at ${h.stackRoot}`);
    h.info('type /help for commands, /exit to leave');
    process.stdout.write('\n');
  }

  const prompt = () => `${glyph.prompt} ${c.gray('traaviis')} ${c.amber('›')} `;
  rl.setPrompt(prompt());
  rl.prompt();

  for await (const line of rl) {
    const trimmed = line.trim();
    if (trimmed) {
      // bare text without a leading slash is treated as a command too
      await h.run(trimmed);
    }
    if (h._exit) break;
    rl.setPrompt(prompt());
    rl.prompt();
  }
  rl.close();
  if (process.stdout.isTTY) h.out(c.gray('\n  the harness is yours. ' + glyph.prompt + '\n'));
}
