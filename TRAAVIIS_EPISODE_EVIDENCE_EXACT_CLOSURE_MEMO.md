# TRAAVIIS — Episode Evidence **Exact** Closure (memo for GPT-5.6)

Date: 2026-07-24. Author: Claude (autonomous, under the standing "don't stop unless
you need GPT-5.6" instruction). This memo + the accompanying standalone archive
cover the **Episode Evidence Exact Closure** you ruled after empirically verifying
the Reopen Closure packet and finding three load-bearing blockers (one a write-path
traversal vulnerability) plus two smaller corrections. It is additive on top of
`TRAAVIIS_EPISODE_EVIDENCE_REOPEN_MEMO.md` (which stays valid as the prior slice).

**Full battery green on this machine (real TRVM engine reachable):**
`256 passed, 0 skipped, 0 failed` across 17 modules
(`test_episode_evidence.py` went 40 → 56; all other modules unchanged and green).

The corrected running total: the Reopen slice was **240** (not the `250` the Reopen
memo stated); this slice's 16 new episode-evidence tests bring the suite to **256**.

Per-module tally (17 modules):
`admission 16 · cli_evalone 18 · cli 12 · episode_evidence 56 · evalone 14 ·
execfacts 8 · forge_adapter 6 · identity 25 · patchapply 10 · paths 10 ·
real_residency 9 · reward 17 · runner 10 · snapshot 10 · substrate_verifiers 15 ·
vcontext 5 · verifiers 15` = **256**.

---

## 1. The 9 ruled steps — where each landed

### Step 1 — freeze + validate `SignalIDV1`
New module `traaviis/signals.py` freezes the grammar `^[a-z][a-z0-9_]{0,63}$`
(`SIGNAL_ID_VERSION = traaviis.signal-id.v1`). It admits every existing signal
(`citations`, `patch`, `tests`, `identity`, `finding_completeness`, `native`,
`oracle`) and rejects any name carrying a path separator, `..`, a drive letter,
whitespace, or uppercase. A signal id is a receipt/manifest key **and** the stem of
`evidence/verifiers/<signal>.json`, so an unconstrained name is a filesystem-write
primitive; this grammar is validated at every admission boundary.

Validated in `evalone.evaluate` (reward/required/not_applicable/extra_verifier
signals, before any path is built) and in `verify_episode_bundle` (every reward /
plan / receipt / evidence / manifest key — an illegal id in a saved bundle is
tampering and fails `checks["closure"]`).

Battery: `test_signal_id_grammar` (unit law),
`test_signal_path_traversal_cannot_write_outside_staging` (drives `_populate`
directly with `../../escape`, `/tmp/escape`, `a/b`, `a\b`, `Upper`, `has space` —
each raises `SignalIDError` before any bytes are written and nothing named `escape`
appears anywhere under the parent), `test_valid_snake_case_signal_accepted`.

### Step 2 — safe output joins for every dynamic evidence path
`episode_bundle._populate` now validates the signal id **and** builds the evidence
path through `paths.safe_join(root, rel)` before writing — a traversal name can
never resolve outside the staging directory even if the grammar were bypassed.

### Step 3 — evidence + version coverage over every non-pseudo declared verifier
`evalone` now separates **scored** signals (`reward_spec.signals`, which drive the
reward number) from **evidence** signals:

```
evidence_signals = (scored ∪ required ∪ not_applicable) − {native, oracle}
```

`build_receipt_v1` seals `verification_evidence` + `verifier_versions` over
`evidence_signals`, so a **required but unweighted** gate has its implementation
version + evidence digest enter `episode-…` — while the reward number stays scored
from `reward_spec.signals` alone. `verify_episode_bundle` recomputes the same
`evidence_signals` and requires exact key equality across manifest verifier keys,
receipt `verification_evidence` keys, and that set.

Battery: `test_required_unweighted_gate_is_fully_sealed`,
`test_unweighted_gate_implementation_drift_is_unavailable`,
`test_unweighted_gate_evidence_tamper_rejected`,
`test_unweighted_gate_state_change_moves_episode_id`,
`test_unweighted_gate_does_not_change_reward` (gate pass **and** gate fail both
yield the same reward as the gate-free demo, yet a failing required gate is still a
valid episode — only a required NA invalidates).

### Step 4 — exact episode-directory tree closure
New `_verify_tree_closure(root_real, members, snapshot)`: the manifest defines the
**complete** bundle. The expected regular-file set is the fixed members ∪ the
snapshot's subject files ∪ persisted verifier evidence ∪ process evidence. The
on-disk tree must equal it exactly. Any undeclared file, symlink (file **or**
directory), socket, device, or FIFO fails `checks["tree"]` → outcome `mismatch`.

Battery: `test_extra_root_file_rejected`, `test_extra_evidence_file_rejected`,
`test_extra_subject_file_rejected`, `test_unreferenced_symlink_rejected`,
`test_special_file_rejected` (FIFO; skips where `os.mkfifo` is absent),
`test_exact_tree_closes` (positive).

### Step 5 — parent-directory fsync after publication
After `os.rename(tmp, final)`, `write_episode_bundle` now fsyncs `dest_root` (the
parent) best-effort, so the new directory entry itself survives a crash right after
the rename on platforms that support directory fsync.

Battery: `test_parent_publication_survives_reopen` (the published bundle reopens
and re-verifies `closed` repeatedly and by an independent second read).

### Step 6 — execution-facts attestation classification
New constant + report field:
```
EXECUTION_FACTS_ATTESTATION = {
  "rederived":       ["agent_process", "sandbox"],
  "runner_attested": ["runner", "platform", "toolchain"],
}
```
Only `agent_process` + `sandbox` are independently rederived from the saved
evidence (the trace's `exit_code` + the manifest's truncation flags; the runner
profile). `runner` / `platform` / `toolchain` are carried as the runner's own
attestation — closure proves internal consistency with that attestation, **not** its
external historical truth. Every `verify_episode_bundle` report (closed or failing)
carries this classification.

Battery: `test_report_carries_execution_facts_attestation`.

### Step 7 — corrected test totals
The Reopen memo's `250 passed` was wrong; the actual Reopen total was **240**. This
memo states **256** and the per-module tally above. The auto-memory Reopen entry is
corrected in the same pass.

### Step 8 — standalone packet
Rebuilt self-contained (full `traaviis/` package + `test/` + `test/fixtures/` +
`examples/eval-one/` including the regenerated conformance episode + both prior
memos + this memo), so a clean extraction runs `python3 test/test_episode_evidence.py`
(and the whole `test/` suite) with no `ImportError`. Fresh-extraction run verified.

### Step 9 — regenerated the five-signal conformance episode
Re-emitted at
`examples/eval-one/episodes/episode-5aef63c4943d44fa4f4703435dbac87bb23fd05e1f8679189b19ca0c3688b3e2/`.
The id is **unchanged** from the Reopen slice: for `residency-forge` the required
set is exactly the five scored signals and the only not_applicable entries are the
pseudo-signals `native`/`oracle`, so `evidence_signals == scored` and the evidence /
version maps (hence the episode id) do not move. It re-verifies **closed, exit 0**
(closure ✓ · artifacts ✓ · all five signals ✓ · reward ✓ 1.0 · episode-id ✓).

---

## 2. Autonomous decisions flagged for your review

1. **scored vs evidence_signals split.** An unweighted required gate seals its
   implementation version + evidence digest into `episode-…` (so drift → unavailable
   and evidence tamper → mismatch), but does **not** change the numerical reward
   (`reward.score` weights only `reward_spec.signals`). A failing required gate is a
   *valid* episode; only a required *not_applicable* invalidates.
2. **Tree closure ignores empty directories** and treats any non-regular node
   (symlink, FIFO, socket, device) as a failure — referenced or not. Directory
   symlinks are flagged explicitly.
3. **Parent-dir fsync is best-effort** (`except OSError: pass`) — a platform without
   directory fsync degrades to the pre-existing durability, never an error.

Everything above is green with the real engine. No blocker — per your ruling I will
proceed **directly to TestPlanV2** without another broad architecture review.
