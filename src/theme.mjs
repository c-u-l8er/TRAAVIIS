// theme.mjs — ANSI palette + minimal terminal styling primitives.
// Zero dependencies. Honors NO_COLOR and non-TTY output.

const enabled = process.stdout.isTTY && !process.env.NO_COLOR;

const code = (n) => (s) => (enabled ? `\u001b[${n}m${s}\u001b[0m` : String(s));

export const c = {
  reset: '\u001b[0m',
  bold: code(1),
  dim: code(2),
  italic: code(3),
  underline: code(4),
  // foreground
  black: code(30),
  red: code(31),
  green: code(32),
  yellow: code(33),
  blue: code(34),
  magenta: code(35),
  cyan: code(36),
  white: code(37),
  gray: code(90),
  // bright accents — the TRAAVIIS palette
  amber: code('38;5;214'),
  teal: code('38;5;43'),
  violet: code('38;5;141'),
  rose: code('38;5;204'),
};

// Status glyphs reused across the harness.
export const glyph = {
  ok: c.green('●'),
  warn: c.amber('●'),
  bad: c.red('●'),
  idle: c.gray('○'),
  arrow: c.gray('▸'),
  bullet: c.gray('·'),
  prompt: c.amber('&'),
};

export function rule(width = process.stdout.columns || 72) {
  return c.gray('─'.repeat(Math.min(width, 72)));
}

export function badge(label, kind = 'idle') {
  const map = { ok: c.green, warn: c.amber, bad: c.red, idle: c.gray };
  const paint = map[kind] || c.gray;
  return paint(`[${label}]`);
}

export function banner() {
  const t = c.amber;
  const d = c.gray;
  return [
    t('  ████████╗██████╗  █████╗  █████╗ ██╗   ██╗██╗██╗███████╗'),
    t('  ╚══██╔══╝██╔══██╗██╔══██╗██╔══██╗██║   ██║██║██║██╔════╝'),
    t('     ██║   ██████╔╝███████║███████║██║   ██║██║██║███████╗'),
    t('     ██║   ██╔══██╗██╔══██║██╔══██║╚██╗ ██╔╝██║██║╚════██║'),
    t('     ██║   ██║  ██║██║  ██║██║  ██║ ╚████╔╝ ██║██║███████║'),
    t('     ╚═╝   ╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═╝  ╚═══╝  ╚═╝╚═╝╚══════╝'),
    d('  a minimal terminal harness for the [&] Protocol stack'),
  ].join('\n');
}
