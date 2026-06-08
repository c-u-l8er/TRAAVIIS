// Minimal zero-dependency tests for the harness core.
import assert from 'node:assert/strict';
import { Harness, tokenize, parsePipeline } from '../src/harness.mjs';
import { registerBuiltins } from '../src/builtins.mjs';

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

console.log(`\n  ${pass} passed`);
