# Portable Subject-Mode Closure — memo

**Slice:** the correction ruled onto the top of the identity ladder, not a new
rung. **Status:** shipped.

**Battery.** D31–D40 with the engine present: **10 passed / 0 skipped /
0 failed**; engine absent: **4 passed / 6 skipped / 0 failed** (D32, D37, D38
and D40 are recipient-side laws and need no Forge checkout — see §3). D1–D40:
**40/40** engine present, 16/24 engine absent. Whole tree: **474 passed /
0 skipped / 0 failed** across 26 files.

Ruled by the sixth GPT-5.6 ruling, which accepted `bundle-…` and then reproduced
the caveat §6 of the previous memo had flagged:

```
source file mode:       0664
manifest mode:          0644
bundle verification:    passes
canonical ZIP extract:  0644
same bundle_id:         yes          ← and yet
different snap-:        yes          ← so a different env-
```

```
pack directory        → bundle verifies, environment verifies
archive-bundle        → normalizes 0664 → 0644
extract archive       → bundle still verifies, environment FAILS to reopen
```

It ruled that a reachable correctness defect, not an acceptable residual, and
ordered it closed now.

---

## 1. Why the previous slice was wrong to ship it as a caveat

Both halves of the tension were individually correct and I could not see a way
to weaken either, so I recorded the residual and moved on. That was the error.
The residual is not a *tension between two identities* — it is a **package that
cannot survive its own archive**, and neither identity has to move to say so.
The fix lives in a third place: **admission**. A mode that cannot be transported
is refused before either id is minted.

Stated the other way round: `snap-…` sealing the exact mode is a claim about a
repository, and `bundle-…` carrying the canonical mode is a claim about a
distribution. There is no contradiction, only a set of repositories that are not
distributable. TRAAVIIS packages distributions. So that set is inadmissible.

## 2. What was built

| file | change |
| ---- | ------ |
| `traaviis/substrates.py` | `CANONICAL_SUBJECT_MODES`, `canonical_subject_mode`, `require_canonical_subject_modes`; called from **both** Residency seams. |
| `traaviis/bundle.py` | `write_archive` gated on a serialized round trip; new `_verify_serialized`; new refusal `BUNDLE_ARCHIVE_ROUNDTRIP`; report gains `roundtrip_verified`. |
| `traaviis/scaffold.py` | `SCAFFOLD_FILE_MODE`; every scaffolded file written at an explicit `0644`. |
| `traaviis/cli.py` | `eval-one` relabels the legacy field **"episode evidence"**; `archive-bundle` prints the round-trip line. |
| `traaviis/evalsplit.py` | `EvaluationV1.bundle` documented as a legacy episode-member field with a frozen value law. |
| `test/test_bundle.py` | D31–D40. |
| `RFC_TRAAVIIS_ARTIFACTS.md` | new **§5c** freezes both laws; §5b gains the `EvaluationV1.bundle` paragraph. |
| `README.md` | archive publication, a subject-mode section, and the legacy-field note. |

### The admission law

For `residency.repository.v1`, every file whose mode enters
`SnapshotV1.file_modes` must already be exactly `0644` or `0755`:

```json
{"paths": {"src/tool.py": {"observed": "0664", "required": "0644"}}}
```

typed **`SUBJECT_MODE_NONCANONICAL`**, raised from one shared helper called by
`recompute_subject_identity` (authoring) and `_reopen_subject` (reopening,
therefore also `reopen_package` and therefore `verify_bundle`). That covers all
three ways a nonportable package can appear: authored here, hand-built
elsewhere, or authored before the law existed.

The helper reads `SnapshotV1.file_modes` rather than walking the tree. That is
the whole of D35: `file_modes` is already post-exclusion and already
symlink-free, so an excluded file is out of scope **by construction** rather than
by a second glob match that could drift away from the first.

`f"{mode:04o}"` renders set-ID modes as `4755` / `2755` / `1644`, which are
outside a two-element allowlist and are therefore refused without needing to be
enumerated. The law is stated as an allowlist for exactly that reason.

### The archive publication law

```
verify source tree
→ write a temporary archive
→ extract it to a temporary directory
→ verify bundle closure from the extracted bytes
→ when not --package-only, re-derive env- and subject closure too
→ compare bundle_id and env_id
→ atomically publish
```

The old order verified the *directory* and then serialized it, which proves the
directory — not the artifact anyone receives. Anything serialization normalizes
away was invisible to it, which is precisely how the mode defect could reach a
published archive. A failed round trip raises `BUNDLE_ARCHIVE_ROUNDTRIP` and
leaves no archive at the output path.

## 3. D31–D40

| # | law | test |
| - | --- | ---- |
| D31 | a Residency subject mode `0664` is refused | `test_d31_a_noncanonical_residency_subject_mode_is_refused` |
| D32 | `0600` / `0640` / `0775` / set-ID modes are refused too | `test_d32_every_other_noncanonical_mode_is_refused_too` |
| D33 | canonical `0644` subjects survive directory→ZIP→extract→verify | `test_d33_canonical_0644_subjects_survive_directory_zip_extract_verify` |
| D34 | canonical `0755` subjects survive the same path | `test_d34_canonical_0755_subjects_survive_the_same_path` |
| D35 | excluded files' modes do not affect admission | `test_d35_excluded_files_with_noncanonical_modes_do_not_affect_admission` |
| D36 | presentation modes may normalize without moving `env-…` | `test_d36_presentation_modes_may_normalize_without_moving_the_environment` |
| D37 | publication verifies the extracted archive before rename | `test_d37_archive_publication_verifies_the_extracted_archive_before_rename` |
| D38 | a failed round trip leaves no final archive | `test_d38_a_failed_archive_roundtrip_leaves_no_final_archive` |
| D39 | ZIP and tar full *environment* verification agree | `test_d39_zip_and_tar_full_environment_verification_agree` |
| D40 | D1–D30 and the packet gates remain green | `test_d40_the_earlier_laws_and_the_packet_gates_are_untouched` |

D31 additionally proves both non-authoring seams: a hand-built package whose
seal is *self-consistent* at `0664` is refused at reopen, and a scaffold written
under `umask 002` still packs (see §4).

D37 is observed at the publication seam — it spies `verify_bundle` and
`os.replace` and asserts that everything verified before publication was an
**extraction**, never the source directory. D38 drives both failure modes: a
closure failure out of the extraction, and a round trip that verifies cleanly as
some *other* package, which is the branch that catches silent normalization.

D40 is a completeness check, not a re-run: D1–D30 are re-run by the battery
itself, in the same process, every time. What D40 asserts is that no earlier law
was dropped or renamed (the D-numbers are exactly 1..40), that the source-release
gate learned nothing about either new code, and that `TrvmWorldV1` did **not**
inherit the mode law while `ResidencyRepositoryV1` enforces it at exactly two
seams.

## 4. A defect this slice created and then closed

Adding the admission law immediately broke `trvs init` on any machine with a
loose umask. `scaffold.materialize` wrote every file at the process umask
default, so under `umask 002` a freshly scaffolded environment is `0664` and
`trvs pack` now — correctly — refuses it. Every author on a `002` machine would
have been locked out by their first two commands.

It was invisible here only because this machine runs `umask 022`, so the default
coincided with the declared mode. Exactly the same shape as the latent defect
the previous slice closed, from exactly the same cause: a template's modes are
part of the template, and were being taken from the machine that unpacked it.

Closed by writing every scaffolded file at an explicit `SCAFFOLD_FILE_MODE =
0o644` (and the scaffold root at `0755`, so `init` under a restrictive umask
does not emit a `0700` environment either). D31 pins it with a real
`os.umask(0o002)` scaffold that must still pack to the reference `env-…`.

## 5. `EvaluationV1.bundle` — kept, relabelled

Ruled: do **not** rename it. Done, and the strengthened D27 reading was
confirmed correct. What changed is only ambiguity:

- `trvs eval-one` prints **`episode evidence: …`**, on both the human and the
  `--json` stderr line. It no longer prints the word "bundle" for this field.
- `evalsplit.py` documents it as a **legacy episode-member field** with a frozen
  value law: `null`, or exactly one `episode-<id>` directory name.
- RFC §5b and the README say the same, and both name `EvaluationV2` →
  `episode_member` as the eventual rename.
- No `EvaluationV2` was introduced.

D27 remains the enforcement: no `bundle_id` key and no `bundle-…` value
anywhere, **and** every surviving `bundle` field must hold an `episode-…`
directory.

## 6. Autonomous decisions (flagged for review)

1. **`_reopen_subject` checks the declared snapshot's modes *before* comparing
   rebuilt bytes.** A transported legacy package is then diagnosed
   `SUBJECT_MODE_NONCANONICAL` — true, and actionable — rather than
   `REOPEN_SUBJECT_BYTES`, which would blame the tree for a defect in the seal.
2. **`write_archive`'s existing `verify` flag is the ruling's `package_only`.**
   `verify=False` ⇒ the round trip checks bundle closure only; `verify=True` ⇒
   it re-derives `env-…` and subject closure too. No second flag was added for a
   distinction that already had one.
3. **`scaffold.materialize` writes an explicit `0644`.** Not ruled, but the
   admission law is unusable without it (§4).
4. **The scaffold root is chmod'd `0755`.** Directory modes are identity-bearing
   nowhere; this only stops `init` from emitting a `0700` environment under a
   restrictive umask.
5. **D31's reopen half calls `_reopen_subject` directly** rather than
   reconstructing a fully self-consistent nonportable package (which would mean
   re-minting every `task-…`, the manifest and `env-…` around a new `snap-…`).
   It is the real seam `reopen_package` uses, with a genuinely self-consistent
   `0664` seal — the surgery would have proven the same thing about the same
   function.
6. **`write_archive` reports `roundtrip_verified`.** A claim that is made should
   be legible; D25's exact-key assertion was widened to admit it.
7. **The staging extraction lives beside the output**, in the same directory the
   temporary archive already used, so publication needs no cross-filesystem
   rename. Both are cleaned up on every path, and D38 asserts no `.trvs-archive-`
   leftovers.

## 7. Cost

`archive-bundle` now extracts and re-verifies every archive it writes, and with
`env-…` verification that means a full substrate reopen per archive. That is
real work for a command that used to be a serializer. It is the price of the
claim: without it, "this archive contains this package" was an inference from
the directory rather than an observation of the bytes.

## 8. Not started (deferred by ruling)

Next is the **Episode Kernel Closure** (`EpisodeKernelV1`, K1–K18) — extract and
freeze the substrate-neutral kernel *before* exposing HTTP, with Residency v1
supporting only `start` + `finalize` and returning
`KERNEL_OPERATION_UNSUPPORTED` for `observe` / `step` / `reset` rather than
pretending to succeed as no-ops. Then `trvs serve --ors` as a translation layer.

Still deferred: batch-evidence distribution identity, `eval-…`, `agent-…`, ORS,
MCP, the REPL, `EvaluationV2`.
