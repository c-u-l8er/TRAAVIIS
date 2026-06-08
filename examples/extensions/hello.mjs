// Example extension. Copy to ~/.traaviis/extensions/ (or a project
// .traaviis/extensions/ dir) and `hello` + `deploy` become first-class
// commands. This is the whole extensibility story: register a command.
//
// `export const name` names the plugin in `/plugins`. A command may declare a
// `capability` it provides and the `needs` it composes with.

export const name = 'hello-example';

export default function (h) {
  h.command({
    name: 'hello',
    summary: 'example user extension',
    capability: 'demo.hello',
    run(h, args) {
      h.out('  hello, ' + (args[0] || 'harness') + ' — you are holding the steering wheel.');
      return { greeted: args[0] || 'harness' };
    },
  });

  // Composes with the built-in `build` command — declared via `needs`, invoked
  // via `h.invoke`, and returns a pipe-able value.
  h.command({
    name: 'deploy',
    summary: 'build then ship a product (demo of composition)',
    needs: ['build'],
    async run(h, args, input) {
      const name = args[0] || (input && (input.name || input));
      const p = h.stack().products.find((x) => x.name === name);
      if (!p) return h.error(`no product matching "${name}"`);
      const built = await h.invoke('build', [p.name]);
      h.out('  ▸ would deploy ' + p.name);
      return { deployed: p.name, built: built?.ok };
    },
  });
}
