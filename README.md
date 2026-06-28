# TRAAVIIS

**The harness engineer for the [&] Protocol stack.**
Pluggable, composable, extensible — it builds the harness around your workflow,
not the other way around.

There are many agent harnesses; traaviis builds yours. Give it a goal and the
runtime routes each prompt to the right model tier, orchestrates sub-agents
across the whole [&] ecosystem — Graphonomous, PRISM, PULSE, box-and-box, and
every spec site — composes their structured results into one pipe, and clears
every action through a governance verdict. It discovers your products live,
reports health, and delegates `build` / `test` / `validate` to each project's
own toolchain. Zero runtime dependencies; small, readable ESM.

It is the terminal embodiment of **OS-008, the Agent Harness Protocol** — the
agent's *runtime*, which is exactly where prompt-routing and orchestration
belong (while the commands and libraries it drives stay model-free). For the
design rationale and the road to a state-of-the-art harness, see
[ARCHITECTURE.md](ARCHITECTURE.md). Home: **traaviis.com**.

## Install

```sh
npm install -g traaviis      # or: pnpm add -g traaviis · bun add -g traaviis
npx traaviis                 # one-off, no install
```

Requires Node ≥ 18.

## Use

```sh
traaviis                     # interactive REPL
traaviis status             # one-shot (print mode)
traaviis --json products    # structured output for CI / agents
```

Inside the REPL, every command is a slash command (the leading `/` is optional):

| command            | what it does                                            |
| ------------------ | ------------------------------------------------------- |
| `/status` `/st`    | stack health overview                                   |
| `/products` `/ls`  | list discovered products (pipe-able array)              |
| `/specs`           | list `.ampersand.json` specs (pipe-able array)          |
| `/info <name>`     | detail for one product                                  |
| `/build <name>`    | run the product's build toolchain (`mix` / `npm`)       |
| `/test <name>`     | run the product's test toolchain                        |
| `/validate <file>` | govern spec(s) via box-and-box `govern` (gates exit code) |
| `/memory`          | graphonomous-first memory loop status                   |
| `/plugins`         | loaded extensions, capabilities, and unmet needs        |
| `/tree`            | show the session as a tree                              |
| `/export [file]`   | write the session history to JSON                       |
| `/mode <m>`        | switch output mode: `interactive` \| `print` \| `json`  |
| `/help` `/?`       | list commands                                           |

### Composition primitives

`where`, `map`, `each`, `first`, `count`, `json` operate on the structured
value flowing through a pipe:

```sh
traaviis "products | where kind=node | map name | each test"
traaviis "specs | validate | where verdict!=decision"
traaviis --json "products | where governed | count"
```

Every data command returns its structure (so it can be piped) and renders only
when it's the terminal stage — the same command is a human view, a `--json`
API, and a pipe source.

## Four ways in

- **interactive** — a REPL with slash commands, tab completion, a session tree
- **print** — `traaviis <cmd>` runs once and exits with human output
- **json** — `traaviis --json <cmd>` emits structured output
- **library** — `import { createHarness } from 'traaviis'` and embed it

## Pointing it at a stack

TRAAVIIS resolves the stack root in this order:

1. `$TRAAVIIS_STACK_ROOT`
2. the parent directory of the package (when checked out inside an [&] repo)
3. the current working directory

It classifies any subdirectory carrying a marker — `mix.exs`, `package.json`,
`docs/spec/`, or `*.ampersand.json` — and reports its kind, version, and a
heuristic health dot (governed ● / spec'd ● / bare ○).

### Pointing it at the governance kernel

`/validate` delegates to the box-and-box `govern` CLI. TRAAVIIS resolves it in
this order (fail-closed — if none resolve, validate exits non-zero rather than
silently passing):

1. `$TRAAVIIS_KERNEL_PATH` — a `govern.mjs` file, a box-and-box package dir, or
   the package root (TRAAVIIS appends `bin/govern.mjs`)
2. an installed `box-and-box` npm package, resolved from this module
3. the sibling checkout in the stack (`AmpersandBoxDesign/box-and-box`)

`/validate` **gates**: the harness process exit code mirrors the kernel verdict
across all targets (the worst wins), so CI can fail-closed on a DENY:

| exit | verdict          | meaning                               |
| ---- | ---------------- | ------------------------------------- |
| 0    | `decision`       | a decision was made — proceed         |
| 1    | `no-admissible`  | every option vetoed — DENY            |
| 2    | `parse-error` / `missing` / no kernel | malformed / not found |
| 3    | `escalation`     | obligation unmet — escalate           |

```sh
traaviis --json "specs | validate" || echo "governance gate failed"
TRAAVIIS_KERNEL_PATH=/opt/box-and-box traaviis validate agent.ampersand.json
```

## Change the harness, not your workflow

There is no plugin API beyond *"register a command."* Drop a `.mjs` file in
`~/.traaviis/extensions/` (user-global) or `<stack root>/.traaviis/extensions/`
(project-local) and it becomes a first-class command — the same registry the
built-ins use. Reuse a built-in's name to override it.

```js
// ~/.traaviis/extensions/deploy.mjs
export const name = 'deploy';          // names the plugin in /plugins
export default function (h) {
  h.command({
    name: 'deploy',
    summary: 'build then ship a product',
    capability: 'ops.deploy',          // pluggability: resolvable by capability
    needs: ['build'],                  // composability: depends on a built-in
    async run(h, [name], input) {
      const p = input ?? h.stack().products.find((x) => x.name === name);
      await h.invoke('build', [p.name]); // reuse another command
      return { deployed: p.name };       // pipe-able return value
    },
  });
}
```

You can also point `$TRAAVIIS_EXTENSIONS` at extra files or directories
(colon-separated). See `examples/extensions/hello.mjs`.

## Primitives, not features

The kernel is a command registry, stack discovery, a session tree, and output
modes, driven by an orchestration loop that routes prompts and fans out
sub-agents (see [ARCHITECTURE.md §3a](ARCHITECTURE.md)). Everything else is an
extension over those primitives. Deliberately left out: baked-in MCP, *hidden*
sub-agents (the fan-out is always visible in the session tree), permission
popups, plan mode, built-in to-dos. Reach for an extension — or Graphonomous,
or `box-and-box govern` — instead.

## Develop

```sh
npm test                     # zero-dependency unit tests
node bin/traaviis.mjs status # run from source
```

MIT licensed · TRAAVIIS Holdings · part of the [&] Protocol ecosystem.
