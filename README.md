# TRAAVIIS

**Evidence-grade environments for evaluating agents.**
Write the wall. Run the world. Keep the proof.

TRAAVIIS provides **evidence-grade environments for evaluating agents** and
proves what happened inside them. TRVM worlds are its strongest deterministic
substrate; **Evidence Residency** is its first repository-evidence substrate. In
the TRVM world substrate you write a world in **WallRiderLang**; `trvs` lowers it
to a content-addressed `SemanticArtifactID`, folds each episode into a replayable
**film**, and verifies that film with every applicable check: a pure reference
reducer, a compiled native reducer, and an independent oracle where its domain
applies. Coverage is made explicit — a verifier that cannot apply is reported
`not_applicable`, never as pass or fail. Same world, same scenario, same
trajectory — same hash, every time.

`trvs` carries **no world semantics of its own**. It is a thin, honest terminal
over the existing Forge/TRVM engine (the `wrl_*.py` identity/lowering spine plus
the `ic_ref` and `ic32` reducers). **TRAAVIIS does not embed, select, or route a
model.** The shipped world commands call none; evaluation (`eval-one`) runs an
agent command *supplied by the user*, which may itself use a model.

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

Eight commands ship today and fold real worlds over the engine. The
**environment surface** turns a subject into something an agent can be evaluated
against; its **beachhead — `trvs eval-one`** — now ships: a one-shot,
trusted-local evaluation of a single frozen subject that admits the bundle,
binds the subject byte-exactly, runs a user-supplied agent, and returns a
content-addressed `episode-…` receipt. Batch evaluation, packaging and serving
come after.

| command         | what it does                                             | status         |
| --------------- | -------------------------------------------------------- | -------------- |
| `trvs doctor`   | engine location, versions, verifier availability         | shipped        |
| `trvs id`       | the world's `SemanticArtifactID` — pure identity         | shipped        |
| `trvs inspect`  | actors, edges, resolved config, diagnostics              | shipped        |
| `trvs run`      | lower + deterministically fold; per-epoch film           | shipped        |
| `trvs verify`   | reference / native / oracle agreement (strict)           | shipped        |
| `trvs replay`   | re-fold a film and assert it reproduces (`--expect`)     | shipped        |
| `trvs diff`     | compare two worlds' identity + per-epoch films           | shipped        |
| `trvs eval-one` | evaluate one agent run over one frozen subject           | shipped        |
| `trvs eval`     | run an agent over a split, score every episode           | after eval-one |
| `trvs init`     | scaffold a new environment subject from a template       | later          |
| `trvs pack`     | package a subject + tasks + rewards into an environment   | later          |
| `trvs serve`    | expose an environment to an agent (`--ors`/`--mcp`)      | later          |

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

### Evaluate one agent run (`eval-one`, shipped)

`trvs eval-one` takes an **eval-bundle** directory (a `bundle.json` manifest that
names the task, reward, snapshot and frozen `subject/`) and a user-supplied agent
command. It admits the bundle before it runs anything — recomputing every
declared id, cross-binding the task to *these* reward + snapshot, binding the
working subject byte-exactly to the sealed snapshot (modes and declared binaries
included), and rejecting any run policy the trusted-local runner cannot honor —
then folds one episode into a content-addressed `episode-…` receipt.

```sh
# the in-repo residency demo, scored against the deterministic stub agent:
trvs eval-one examples/eval-one/residency-demo \
    --agent python3 "$PWD/test/fixtures/stub_agent.py" --platform linux-x86_64

# dashed agent flags pass through unchanged after a standalone `--`:
trvs eval-one examples/eval-one/residency-demo \
    --platform linux-x86_64 -- my-agent --model foo --temperature 0
```

Exit codes mirror the receipt's status: **0** a valid episode, **1** an invalid
one (policy violation / invalid config), **2** admission rejected the bundle
before execution or a substrate error prevented scoring.

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
| `sem-…`     | was it the same subject?        |
| `scen-…`    | same initialization?            |
| `rew-…`     | same scoring rubric?            |
| `task-…`    | same assignment?                |
| `trace-…`   | same behavior?                  |
| `episode-…` | same evaluated outcome?         |
| `env-…`     | same environment release?       |
| `bundle-…`  | same distributed package?       |

A **`trace-…`** is the substrate-neutral observable record; a **`film-…`** is the
*TRVM case* of a `trace-…`. Re-scoring the *same* trace under a different rubric
changes the `episode-…` receipt but never the `trace-…` — the recorded behavior
did not change. The shared evaluation constructs live in the `traaviis.*`
namespace; substrate-specific evidence (TRVM `sem·scen·film`, Residency
`snap·trace·finding·patch`) lives below.

### Evaluation before training

The first job is not a trainer, and the first interface is not a batch — it is a
one-shot `trvs eval-one task.json --agent-command …` over a single frozen
subject. Batch `trvs eval` over a split (with a side-by-side comparison view)
follows once eval-one is boring. Because every episode is verified and
content-addressed, the numbers are reproducible and the traces are re-checkable.

## Flagship worlds

- **Golden Spinner** *(shipped)* — the identity, replay and triple-fold
  tutorial. Installs, verifies, and reproduces byte-for-byte.
- **Evidence Residency** *(next)* — an agent inspects a frozen repository, finds
  one real spec/implementation inconsistency, cites the conflicting evidence,
  proposes the smallest patch, runs the declared checks, and returns a structured
  finding + a re-verifiable receipt. The first end-to-end evaluation environment;
  see `RFC_EVIDENCE_RESIDENCY.md`.
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
| **TaskSpecV1**   | the substrate-neutral assignment + evaluation contract  |
| **WallRiderLang**| the language for TRVM worlds, actors and world rules    |
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
python3 test/test_cli.py               # world CLI battery over the engine
python3 test/test_cli_evalone.py       # eval-one admission + episode battery
python -m traaviis.cli doctor          # run from source
```

MIT licensed · TRAAVIIS Holdings · part of the [&] Protocol ecosystem.
Home: **traaviis.com**.
