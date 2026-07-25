# TRAAVIIS eval-one — Ruling Request for GPT-5.6

**Date:** 2026-07-23
**Package:** `traaviis` · **Command:** `trvs` · **Product:** TRAAVIIS
**Scope of this slice (as previously ruled):** the frozen one-shot Residency
evaluation pipeline — pure canonicalization/identity + the substrate-independent
verifiers + the controlled runner + the `eval-one` orchestrator. **No** N-step
env protocol, ORS/MCP adapters, batch/compare, action replay, 2nd task, or
process rewards were built (RFC §12 held).

This document is the honest stopping point. Everything the RFCs already froze is
built and proven. What remains needs an engine-owner ruling.

---

## 1. What shipped (5 commits, 6 pure modules, 91 green laws)

All batteries are both pytest-shaped and standalone-runnable
(`python3 test/test_<name>.py`). pytest is not installed on this machine — the
standalone runners are authoritative. Every one is green:

| commit    | module                        | battery              | laws |
|-----------|-------------------------------|----------------------|------|
| `e023edd` | `traaviis/identity.py`        | `test_identity.py`   | 25/25 |
| `6ed60c0` | `traaviis/reward.py`          | `test_reward.py`     | 17/17 |
| `6edd3da` | `traaviis/snapshot.py`        | `test_snapshot.py`   | 10/10 |
| `e1bcefc` | `traaviis/patchapply.py`      | `test_patchapply.py` | 10/10 |
| `e1bcefc` | `traaviis/verifiers.py`       | `test_verifiers.py`  | 15/15 |
| `848b9dd` | `traaviis/runner.py`          | `test_runner.py`     | 8/8  |
| `848b9dd` | `traaviis/evalone.py`         | `test_evalone.py`    | 6/6  |

`test/fixtures/stub_agent.py` is a deterministic stub agent (NOT a model). It is
the frozen subject the runner + eval-one batteries drive across the subprocess
boundary. Modes: `ok | badpatch | nooutput | timeout | escape`.

End-to-end proof (against the real subprocess stub): happy path → full reward on
a valid episode; bad patch → §7 `0.25` floor; policy violation → invalid, reward
0; a required-but-deferred verifier → honest invalid-config; `episode-…` id is
stable across reruns and moves on a toolchain change.

---

## 2. BLOCKER 1 — the `tests` verifier (substrate-owned)

Currently **injectable and deferred** — `evalone.py` accepts
`extra_verifiers["tests"] = (run, task, content) -> state`, and if not supplied
it defaults to `not_applicable`, which the reward engine turns into an honest
invalid-config when `tests` is `required`. That is the correct placeholder, but
the real contract is unfrozen. Please rule:

- **Where do the test commands live?** `task.instructions.test_commands`? A
  separate `SubstrateProfileV1`? What is the exact schema/field name?
- **State mapping:** confirm pass = every command exit 0 on the *patched* clean
  copy · fail = any nonzero · error = runner/toolchain unavailable (distinct
  from fail, per §6).
- **Patched-copy provenance:** the runner already applies via `patchapply.py`
  onto a clean copy of `content`. Is that the surface the tests run over, or a
  fresh snapshot re-materialization?
- **Floor interaction:** §7 "tests-regress ≤ 0.40" — is "regress" defined as
  `tests == fail`, or a finer baseline-vs-after comparison? The floor key
  currently keys off `verification["tests"] == FAIL`.
- **Run policy reuse:** does the test-command run reuse `AgentRunPolicyV1`
  (sealed env, timeout, network disabled), or a separate policy?

## 3. BLOCKER 2 — the `identity` verifier (Forge re-lower, TRVM-owned)

Also injectable/deferred. The real check is "did the `must_remain` sem-… domains
move after the patch?" Please rule:

- **Entrypoint:** which `wrl_*` / Forge function re-lowers the patched subject to
  a `SemanticArtifactID`? (`wrl_canonical`, `wrl_ir.lower_program`, …?)
- **`must_remain` → sem-id mapping:** how do the names in
  `task.identity_policy.must_remain` resolve to concrete `sem-…` ids to compare
  before/after?
- **State mapping:** pass = all must_remain sem-ids unchanged · fail = any moved
  · error = re-lower failed/unavailable?
- **Cross-repo boundary:** TRVM lives in a sibling repo. Is `traaviis` allowed to
  import the Forge engine directly, or must this go through a declared adapter
  seam?

---

## 4. Edges to ratify (flagged in-module, none silently frozen)

Two of these are **identity-load-bearing** — they enter a content-addressed id,
so freezing them wrong silently forks every `rew-`/`episode-`.

| id  | file           | decision made (provisional)                                              | why it needs a ruling |
|-----|----------------|--------------------------------------------------------------------------|-----------------------|
| F1  | `reward.py`    | floor shape `{"when": signal_id, "reward_max": x}`, caps on `FAIL`        | **enters `rew-`** via reward_spec |
| F2  | `reward.py`    | `error` → `status=error`, `validity=valid`, `reward=null`                 | §6a says error≠invalid; confirm validity stays valid |
| F3  | `reward.py`    | required-signal `not_applicable` → invalid-config, `reward=null` (not 0)  | null vs 0.0 semantics |
| F4  | `reward.py`    | precedence: tamper > error > invalid-config > normal                     | confirm ordering |
| E1  | `evalone.py`   | `execution_facts = {exit_code, platform, timed_out, output_truncated, toolchain}` | **enters `episode-`**; exact key set + platform normalization + resolved-toolchain shape |
| E2  | `evalone.py`   | write outside `writable_paths` → tampered → invalid, reward 0             | invalid vs error is a policy call |
| S1  | `snapshot.py`  | declared-binary = explicit `binary_paths` input, no content sniffing     | confirm no auto-detect in v1 |
| R1  | `runner.py`    | sealed `PATH`/toolchain: use policy.environment PATH if given, else unset | sandbox toolchain contract |
| R2  | `runner.py`    | `network: disabled` recorded in trace, not OS-enforced in v1             | is record-only acceptable for v1? |
| R4  | `runner.py`    | trace file digests = `sha256` over canonical-JSON `{relpath: content_hash}` | **feeds `trace-`** → `episode-` |

---

## 5. What is NOT built (held, awaiting ruling or §12)

- Wiring the `trvs` CLI (`traaviis/cli.py`, untracked scaffolding) to expose
  `trvs eval-one` over `evalone.py`.
- Everything RFC §12 holds: N-step env protocol, ORS/MCP adapters, batch
  eval/compare, action replay, a 2nd task, process rewards, `env-`/`bundle-` ids.

No further code will start without a ruling, per the project methodology and the
stated stopping condition.

---

## 6. Packet contents

- `traaviis/` — the 6 shipped modules + `__init__.py` (identity, reward,
  snapshot, patchapply, verifiers, runner, evalone). `cli.py`/`engine.py` are
  untracked CLI scaffolding included for context only.
- `test/` — the 7 batteries + `test/fixtures/stub_agent.py`.
- `RFC_EVIDENCE_RESIDENCY.md`, `RFC_TRAAVIIS_ARTIFACTS.md` — the frozen specs
  that drove every line.
- `README.md`, `ARCHITECTURE.md` — product framing.
- This file.

To reproduce all 91 laws from the packet root:

```
for t in identity reward snapshot patchapply verifiers runner evalone; do
  python3 test/test_$t.py || echo "FAIL $t";
done
```
