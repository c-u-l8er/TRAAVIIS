# TRAAVIIS — Architecture & the road to a SOTA terminal harness

> **Thesis.** A state-of-the-art terminal harness is not the one with the most
> features. It is the one with the smallest kernel and the sharpest seams — the
> one that is most **pluggable**, **composable**, and **extensible**. Features
> are liabilities; primitives are leverage. TRAAVIIS is designed so that
> everything a user wants is *assembled* from a handful of primitives rather
> than *shipped* as a fixed menu.

> **TRAAVIIS is the harness engineer.** It is not a fixed harness you adapt to;
> it is the runtime that *builds* the harness for the job in front of you. Give
> it a goal and it routes each prompt to the right model tier, decomposes the
> work into sub-agents, drives them over the [&] stack through the same kernel,
> composes their structured results into one pipe, and clears every action
> through a governance verdict. The harness is the agent's runtime — so the
> harness is exactly where prompt-routing and orchestration *belong*.

This document is the result of researching how the leading harnesses are built,
extracting the patterns that actually carry their weight, and mapping them onto
a kernel for the [&] Protocol stack.

---

## 1. How to research "SOTA terminal harness"

The honest way to find the state of the art is to study the systems that have
already paid for their lessons, then isolate the *mechanism* (not the feature
list) behind each one. The reference set:

| System | What to steal | What it teaches |
| --- | --- | --- |
| **Pi** (pi.dev / Earendil) | "Stay small at the core; extend via TS extensions, skills, prompt templates, themes, packages." 4 surfaces: interactive, print/JSON, RPC (JSONL), SDK. | *Primitives, not features.* MCP, sub-agents, plan mode, permission popups are deliberately **not** built in — each is reachable as an extension. |
| **Nushell** | Structured values flow through `\|`. A pipe carries tables/records, not byte streams, so `where`, `get`, `each` compose without re-parsing. | **Composability is a data-model decision.** If every command emits/consumes structured values, composition is free. |
| **Unix shells** | Tiny tools + pipes + a uniform interface (text streams). | The *uniform interface* is what makes the ecosystem open-ended. Pick one and never break it. |
| **Claude Code / aider** | Hooks/events, slash commands, MCP, sub-agents, sandboxed execution, permission gates. | The expensive parts are **policy** (what's allowed) and **provenance** (what happened). Externalize both. |
| **The [&] Protocol itself** | Capabilities compose via a manifest; `box-and-box` issues governance *verdicts*; Graphonomous is the memory loop; PULSE sequences loops. | The stack already has composition, governance, and memory protocols — the harness should *delegate* to them, not reinvent them. |

The synthesis: **a SOTA harness is a uniform-interface kernel plus three seams**
— a *plug* seam (register capabilities), a *compose* seam (pipe structured
values), and an *extend* seam (override/augment by registration). Everything
else — providers, governance, memory, sub-agents — lives behind those seams.

---

## 2. The three axes

### Extensibility — *change the harness, not your workflow*

There is exactly one extension mechanism: **register a command.** An extension
is a `.mjs` file that default-exports `(h) => { h.command({...}) }`. It is the
same registry the built-ins use, so an extension can **override** a built-in by
reusing its name, add a brand-new command, register a theme, or install a hook.

- Lookup order (later wins): built-ins → `~/.traaviis/extensions/` →
  `<stack>/.traaviis/extensions/` → `$TRAAVIIS_EXTENSIONS`.
- No plugin SDK to learn, no manifest format to satisfy, no lifecycle to honor.
  The "API" is the command spec.
- Code: [`src/extensions.mjs`](src/extensions.mjs), [`src/harness.mjs`](src/harness.mjs) `command()`.

### Composability — *pipe commands like a shell*

Every command has the signature `run(h, args, input) -> value`. The return
`value` of one stage becomes the `input` of the next. A line is a **pipeline**
split on top-level `|`:

```
products | where kind=node | map name | each test
specs | validate | where verdict!=decision
```

This is the Nushell lesson applied: the pipe carries **structured values**
(arrays/records), not text, so generic combinators compose with any data
command without parsing.

- Generic combinators ship as ordinary commands: `where`, `map`, `each`,
  `first`, `count`, `json`.
- Data commands (`products`, `specs`, `status`, …) **return** their structure
  and render via `h.present(data, prettyFn)` — which only prints when the stage
  is terminal, and emits JSON in `--json` mode. So the *same command* is a
  human view, a JSON API, and a pipe source.
- Cross-command calls use `await h.invoke(name, args, input)` (no session
  side-effects) — this is how an extension's `deploy` reuses the built-in
  `build`.
- Code: [`src/harness.mjs`](src/harness.mjs) `parsePipeline()`, `run()`,
  `invoke()`, `present()`.

### Pluggability — *capabilities, not hard wiring*

Commands may declare a `capability` they provide and the capabilities they
`needs`. The harness keeps a capability → command index, resolves by capability
as well as by name, tracks every extension as a **plugin manifest**, and can
report **unmet needs**.

```
/plugins        → loaded extensions, the commands + capabilities each adds,
                  the full capability set, and any unmet `needs`
```

This is what lets providers, themes, governance backends, and memory adapters
be swapped without touching the kernel: depend on the *capability*
(`govern.validate`, `stack.build`), not the implementation.

- Code: [`src/harness.mjs`](src/harness.mjs) `capabilities`, `resolve()`,
  `unmetNeeds()`; [`src/extensions.mjs`](src/extensions.mjs) manifest tracking;
  `plugins` built-in.

---

## 3. The kernel — five primitives, nothing more

```
1. command registry      extensibility  (register / override / resolve-by-capability)
2. pipe engine + values  composability  (parsePipeline → run → invoke → present)
3. capability manifests   pluggability   (capability index, needs, plugin list)
4. session history tree   replay/share   (every line recorded; export to JSON)
5. output modes           surfaces       (interactive | print | json)
```

If a proposed behavior cannot be expressed as a command over these five, the
correct response is usually to sharpen a primitive — not to add a sixth.
`src/` is intentionally small and dependency-free; read it top to bottom.

---

## 3a. The orchestration loop — the engineer at work

The five primitives are the materials; the **orchestration loop** is the
engineer that works them. It is *not* a sixth primitive — it is a runtime loop
that drives the five, mirroring the stack's
`retrieve ▸ route ▸ act ▸ learn ▸ consolidate` cadence:

```
goal ─▶ retrieve   pull prior context (Graphonomous)
       ▶ route     pick the model tier + decompose into sub-agents
       ▶ act       each sub-agent runs commands over the kernel (pipe engine)
       ▶ learn     report each outcome back to memory
       ▶ consolidate  merge structured results into one answer + verdict
```

Three properties make this honest rather than magic:

1. **Visible fan-out.** Sub-agents are explicit nodes in the session tree, not
   hidden threads. What runs, runs in front of you and is replayable via
   `export`.
2. **Tier-routed prompts.** Each step is routed to a `provider.*` capability
   honoring MODEL_TIER budgets (`local_small ▸ local_large ▸ cloud_frontier`),
   resolved like any other capability — swap the provider, not the kernel.
3. **Gated acts.** Before a sub-agent acts, the action is piped through
   `govern.validate` (box-and-box) and must clear `feasible ▸ permitted ▸ best`
   over an un-weakenable floor. Autonomy never outruns governance.

### The layer boundary (why this doesn't break "tools don't call models")

The stack rule is that **tools and libraries** — MCP servers, box-and-box,
Graphonomous, PRISM — never call a model; the **agent** does. TRAAVIIS is not
one of those tools. **It is the harness: the agent's runtime.** It is the
"agent" side of that sentence. So routing prompts and calling models is exactly
its job, while every *command* it drives stays model-free and every *library*
it composes stays a pure verdict/memory/measurement function. The boundary is
sharpened, not crossed:

| Layer | Calls a model? | Examples |
| --- | --- | --- |
| **Harness / runtime** (this) | **yes** — routes prompts, orchestrates sub-agents | TRAAVIIS orchestration loop |
| Commands / tools | no | `build`, `test`, `validate`, extensions |
| Libraries / MCP | no | box-and-box, Graphonomous, PRISM |

---

## 4. What we deliberately did NOT build (and the seam to use instead)

Following Pi's "primitives, not features," each omission maps to a seam:

| Not built | Reach for | Why it belongs outside the kernel |
| --- | --- | --- |
| Baked-in MCP | an extension that registers MCP-bridge commands | MCP is one integration, not the harness's identity. |
| *Hidden* sub-agents | the orchestration loop, whose fan-out is **visible** | The harness *does* orchestrate sub-agents (§3a) — but every one is a session-tree node printed in front of you, never a hidden thread. |
| Permission popups | `box-and-box govern` verdicts (`govern.validate`) | Governance is a **verdict + certificate**, not a modal. |
| Plan mode | session-tree nodes you `export` and replay | A plan is just recorded intent. |
| Built-in to-dos | Graphonomous goals | Memory + goals belong to the memory loop, per stack policy. |
| LLM calls inside a *command* or *library* | the harness runtime, which routes prompts on the agent's behalf | Stack policy holds at the tool/library layer; the harness is the runtime where prompt-routing belongs (§3a). |

---

## 5. Roadmap to SOTA (capability-shaped, so each lands as a plug)

1. **RPC / JSONL surface** — a 4th mode: read commands as JSONL on stdin, emit
   structured events on stdout, so TRAAVIIS embeds in editors and agents
   (Pi's RPC lesson). Capability: `surface.rpc`.
2. **Packages** — a `traaviis.plugin.json` that bundles extensions + themes +
   prompt templates, installable from npm/git, surfaced in `/plugins`.
3. **Provider/model adapters** — pluggable `provider.*` capabilities that power
   the orchestration loop's tier routing (§3a), honoring the stack's MODEL_TIER
   budgets (local_small / local_large / cloud_frontier). The runtime calls the
   model; the commands it drives stay model-free.
4. **Governance gate as a hook** — a `beforeRun` hook that runs `box-and-box
   govern` against a declared policy and blocks on `no-admissible`, attaching
   the certificate to the session node. Permissions become *floor-then-verdict*.
5. **Memory loop wiring** — `retrieve` before a stage, `learn` after, via
   Graphonomous capabilities, so the harness participates in the
   retrieve ▸ route ▸ act ▸ learn ▸ consolidate loop the stack already runs.
6. **PULSE manifest** — declare TRAAVIIS's own loop topology
   (`traaviis.pulse.json`) so PRISM can benchmark the harness as a loop.
7. **Themes + prompt templates** — register palettes (`h.theme`) and reusable
   prompt commands, completing Pi's extension taxonomy.

Each item is a *capability behind a seam*, not a new kernel feature — which is
the whole point.
