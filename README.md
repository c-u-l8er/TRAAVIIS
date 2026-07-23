# TRAAVIIS

**Content-addressed agent environments you can verify.**
Write the wall. Run the world. Keep the proof.

TRAAVIIS builds deterministic worlds that agents can be **evaluated — and
eventually trained** — against, and proves what happened inside them. You write a
world in **WallRiderLang**; `trvs` lowers it to a content-addressed
`SemanticArtifactID`, folds each episode into a replayable **film**, and verifies
that film with every applicable check: a pure reference reducer, a compiled
native reducer, and an independent oracle where its domain applies. Coverage is
made explicit — a verifier that cannot apply is reported `not_applicable`, never
as pass or fail. Same world, same scenario, same trajectory — same hash, every
time.

`trvs` carries **no world semantics of its own**. It is a thin, honest terminal
over the existing Forge/TRVM engine (the `wrl_*.py` identity/lowering spine plus
the `ic_ref` and `ic32` reducers). No model is ever called.

> **The differentiation:** not merely "deterministic environments," but
> *content-addressed* ones. Two researchers can answer four questions and never
> argue about the answers — did we run the same world, the same scenario, the
> same trajectory, and did the verifiers agree?

## Install

```sh
pipx install traaviis        # or: pip install traaviis
trvs doctor                  # check the engine + verifiers are on-path
```

**No third-party Python dependencies.** Requires Python ≥ 3.9, a compatible
Forge/TRVM engine reachable at runtime (see `traaviis.engine`), and — for native
verification — the `ic32` executable (otherwise `verify` degrades to the
reference reducer).

## The command set

Seven commands ship today and fold real worlds over the engine. Three more are
the **environment surface** that turns a world into something an agent can be
trained and evaluated against — published as roadmap, not yet built.

| command        | what it does                                             | status  |
| -------------- | -------------------------------------------------------- | ------- |
| `trvs doctor`  | engine location, versions, verifier availability         | shipped |
| `trvs id`      | the world's `SemanticArtifactID` — pure identity         | shipped |
| `trvs inspect` | actors, edges, resolved config, diagnostics              | shipped |
| `trvs run`     | lower + deterministically fold; per-epoch film           | shipped |
| `trvs verify`  | reference / native / oracle agreement (strict)           | shipped |
| `trvs replay`  | re-fold a film and assert it reproduces (`--expect`)     | shipped |
| `trvs diff`    | compare two worlds' identity + per-epoch films           | shipped |
| `trvs init`    | scaffold a new world bundle from a template              | v0.1    |
| `trvs pack`    | bundle a world + scenarios + tasks + rewards             | v0.2    |
| `trvs serve`   | expose the world as an agent environment (`--ors`/`--mcp`) | v0.2  |
| `trvs eval`    | run an agent over a split, score every episode           | v0.3    |

Every shipped command takes `--json` for CI / agent consumption.

## Use (shipped today)

```sh
trvs id      worlds/alley.wrl                 # sem-8ae91fe9…fe4a
trvs inspect worlds/alley.wrl                 # actors, edges, config, diagnostics
trvs run     worlds/alley.wrl                 # per-epoch film strip
trvs verify  worlds/alley.wrl                 # reference · native · oracle → 3/3
trvs diff    worlds/alley.wrl worlds/alley_n4.wrl

# replay pins a *film* — the trajectory, not the world's identity:
film="$(trvs run worlds/alley.wrl --json | python -c 'import json,sys; print(json.load(sys.stdin)["epochs"][-1]["film"])')"
trvs replay  worlds/alley.wrl --film "$film"   # asserts the fold reproduces that film
```

`replay` also accepts `--expect sem-…` to assert the source still lowers to a
pinned **semantic identity** — a different question (same *meaning*) from `--film`
(same *trajectory*). Keep the two domains distinct.

Exit-code contract for `verify` / `replay` / `diff`: **0** agree/reproduced,
**1** ran and disagreed/drifted, **2** a verifier was unavailable or the source
failed to lower. That makes any of them a fail-closed gate in CI or an RL loop.

## The environment surface (roadmap)

A tool list tells an agent what it *can* call; it does not define tasks, splits,
rewards, episode completion, or reset. So TRAAVIIS's internal contract is a
**neutral Episode Kernel** — and public protocols are *adapters* over it, never
runtime law:

```text
Episode Kernel   start · observe · step · reset · finalize   (internal, neutral)
        ↓
ORS adapter      first / primary public surface  →  trvs serve --ors
MCP adapter      compatibility (tools/resources/prompts)  →  trvs serve --mcp
JSONL adapter    local automation / debugging
```

Keeping the kernel neutral means [Open Reward Standard](https://openreward.ai)
or MCP protocol evolution never becomes TRVM runtime law. The ORS wire surface
(`list_tasks · session · call_tool → reward · finished`) and MCP primitives
(`tools · resources · prompts`) are projections of the same kernel.

**Strategy:** do not compete with hosting catalogs. TRAAVIIS aims to be one of
the best ways to *author* deterministic environments that export to them.

### The bundle — `traaviis.environment.v1`

`trvs pack` separates *what an environment means* from *how it is shipped*. The
**environment manifest** (`env-…`) fixes the world, tasks, rewards, action /
observation profiles, and split membership; the outer **package** (`bundle-…`)
carries presentation, docs, and screenshots and may change without moving
`env-…`. A package is **closed** — the embedded world re-lowers to its declared
`sem-…` and every task / reward / scenario reference resolves inside the closure,
or `pack` fails loudly (it re-opens and re-verifies the emitted bundle before
reporting success).

TRAAVIIS freezes an **artifact ladder**, each level answering one question:

| id          | question                        |
| ----------- | ------------------------------- |
| `sem-…`     | was it the same world?          |
| `scen-…`    | same initialization?            |
| `rew-…`     | same scoring rubric?            |
| `task-…`    | same assignment?                |
| `film-…`    | same behavior?                  |
| `episode-…` | same evaluated outcome?         |
| `env-…`     | same environment release?       |
| `bundle-…`  | same distributed package?       |

Re-scoring the *same* film under a different rubric changes the `episode-…`
receipt but never the `film-…` — the trajectory did not change.

### Evaluation before training

The first job is not a trainer — it is honest evaluation. `trvs eval` runs an
agent over a split and scores every episode; a comparison view puts two runs
side by side. Because every episode is verified and content-addressed, the
numbers are reproducible and the films are re-checkable.

## Flagship worlds

- **Golden Spinner** *(shipped)* — the identity, replay and triple-fold
  tutorial. Installs, verifies, and reproduces byte-for-byte.
- **Courier / Factory** *(planned)* — move objects, open gates, route signals,
  spend energy, obey safety constraints, complete deliveries: spatial state,
  long-horizon tasks, objective rewards, resets, splits.
- **WallRider / Graffiti** *(planned)* — an agent moves through a city and writes
  executable tags that alter surfaces, routes and permissions. It proposes graph
  edits; TRVM decides what they mean.

## The product boundary

The seams are frozen on purpose:

| layer            | responsibility                                          |
| ---------------- | ------------------------------------------------------- |
| **TRAAVIIS**     | the product — CLI, environment surface, evaluator, packaging |
| **trvs**         | the command-line interface                              |
| **WallRiderLang**| the language for worlds, actors, tasks and rules        |
| **Forge**        | the compiler, identity and artifact pipeline            |
| **TRVM**         | the deterministic execution substrate                   |
| **Spinner Bench**| the reference workbench and conformance laboratory      |

## What TRAAVIIS is not

- **Not a coding agent** — it builds and proves environments; it is not another
  chat/coding assistant.
- **Not a model router** — nothing here calls an LLM or picks a provider. The
  fold is deterministic; the reward is computed.
- **Not an RL cloud** — worlds run locally; TRAAVIIS authors environments that
  export to hosting stacks, it is not the catalog.
- **Not a game engine** — WallRider worlds are deterministic agent environments,
  not a rendering or physics engine.
- **Not the language or engine** — WallRiderLang defines a world; TRVM/Forge
  lower and fold it. `trvs` holds no world semantics.
- **Not trust-me** — every film is checked by every applicable verifier and
  content-addressed. Disagreement is exit 1, not a warning; a verifier that
  cannot apply is reported `not_applicable`, never as pass or fail.

## Develop

```sh
python -m pytest test/test_cli.py      # CLI tests over the engine
python -m traaviis.cli doctor          # run from source
```

MIT licensed · TRAAVIIS Holdings · part of the [&] Protocol ecosystem.
Home: **traaviis.com**.
