# Bundle Distribution Identity Closure — memo

**Slice:** `bundle-…`, the eighth and topmost rung of the TRAAVIIS identity
ladder. **Status:** shipped. **Battery:** D1–D30, 30 passed / 0 skipped /
0 failed.

Ruled by the fifth GPT-5.6 ruling, verbatim:

> Implement `bundle-…` as the content address of the complete canonical
> environment-distribution tree emitted by `trvs pack`: environment closure plus
> every shipped presentation, documentation and screenshot member, identified by
> normalized path, bytes and mode. Keep ZIP SHA-256 as a transport checksum,
> keep `accept_packet.py` as a source-release gate, and keep EvaluationV1,
> ComparisonV1 and SerialBatchV1 free of bundle identity.

---

## 1. What was built

| file | change |
| ---- | ------ |
| `traaviis/identity.py` | new rung: `canonicalize_bundle` / `bundle_id`. Unlike every other rung nothing is projected out but the id field itself — the manifest *is* the package, so an added field is a changed package. |
| `traaviis/bundle.py` | **new.** `BundleManifestV1`, tree scan, manifest read, bidirectional closure verification, canonical ZIP/tar emission and extraction, transport checksum. |
| `traaviis/pack.py` | the `distribution` block, presentation members, exact-mode member writes, and the ruled staged-reopen-then-publish admission order. |
| `traaviis/scaffold.py` | all three templates declare a `distribution` block. |
| `traaviis/cli.py` | `trvs verify-bundle` (directory **or** archive), `trvs archive-bundle`. |
| `test/test_bundle.py` | **new.** The D1–D30 acceptance battery. |
| `RFC_TRAAVIIS_ARTIFACTS.md` | §5 promoted from DEFERRED; new **§5b** freezes the rung. |
| `README.md` | two command rows and a worked "Ship a package" section. |

`bundle_id = "bundle-" + sha256(canonical(BundleManifestV1 without bundle_id))`,
shipped at the package root as **`TRAAVIIS_BUNDLE.json`** — a name kept
deliberately distinct from the operational per-episode `bundle.json` that
`eval-one` writes.

## 2. The negatives, held

The ruling was as specific about what `bundle-…` is *not*. Each is a test:

- `bundle-… ≠ hash(ZIP bytes)` — D22 (metadata churn moves nothing), D23 (ZIP
  and tar extractions of one tree agree), D25 (the two claims are distinct and
  reported under different names).
- `bundle-… ≠ hash(batch-output/)`, `≠ hash(batch.json)`,
  `≠ hash(all episode results)` — D27 walks every JSON a real two-candidate
  batch produces and finds no `bundle_id` and no `bundle-…` value anywhere.
- `SerialBatchV1` gains no `bundle_id` field — D27.
- Bundle does not supersede or wrap the packet gate — D26 asserts the two
  manifest versions and filenames differ and that neither packet tool mentions
  `bundle-` or `bundle_id`.

## 3. D1–D30

| # | law | test |
| - | --- | ---- |
| D1 | equivalent package trees derive the same `bundle-…` | `test_d1_equivalent_package_trees_derive_the_same_bundle` |
| D2 | `bundle_id` is excluded from its own hash | `test_d2_bundle_id_is_excluded_from_its_own_hash` |
| D3 | member ordering does not move identity | `test_d3_member_ordering_does_not_move_identity` |
| D4 | a member byte change moves `bundle-…` | `test_d4_a_member_byte_change_moves_the_bundle` |
| D5 | a member path rename moves `bundle-…` | `test_d5_a_member_path_rename_moves_the_bundle` |
| D6 | a canonical mode change moves `bundle-…` | `test_d6_a_canonical_mode_change_moves_the_bundle` |
| D7 | name/description move `bundle-…`, not `env-…` | `test_d7_name_and_description_move_the_bundle_not_the_environment` |
| D8 | documentation changes move `bundle-…` only | `test_d8_documentation_changes_move_the_bundle_not_the_environment` |
| D9 | screenshot changes move `bundle-…` only | `test_d9_screenshot_changes_move_the_bundle_not_the_environment` |
| D10 | subject/task/reward/split changes move **both** | `test_d10_subject_task_reward_and_split_changes_move_both` |
| D11 | `environment.json` re-derives its declared `env-…` | `test_d11_environment_json_rederives_its_declared_env_id` |
| D12 | the substrate subject re-derives its declared identity | `test_d12_the_substrate_subject_rederives_its_declared_identity` |
| D13 | every task/reward/profile reference resolves | `test_d13_every_task_reward_and_profile_reference_resolves` |
| D14 | a missing manifested member is refused | `test_d14_a_missing_manifested_member_is_refused` |
| D15 | an extra unmanifested member is refused | `test_d15_an_extra_unmanifested_member_is_refused` |
| D16 | a hash or mode mismatch is refused | `test_d16_a_hash_or_mode_mismatch_is_refused` |
| D17 | unsafe, duplicate and symlinked members are refused | `test_d17_unsafe_duplicate_and_symlinked_members_are_refused` |
| D18 | the manifest excludes itself, with no self-hash fiction | `test_d18_the_manifest_excludes_itself_without_a_self_hash_fiction` |
| D19 | pack writes and publishes the complete tree atomically | `test_d19_pack_writes_and_publishes_the_complete_tree_atomically` |
| D20 | a failed pack leaves no destination or partial bundle | `test_d20_a_failed_pack_leaves_no_destination_or_partial_bundle` |
| D21 | reopening proves **both** identities | `test_d21_reopening_proves_both_bundle_and_environment_identities` |
| D22 | ZIP metadata changes do not move `bundle-…` | `test_d22_zip_metadata_changes_do_not_move_the_bundle` |
| D23 | ZIP and tar extractions of one tree derive the same id | `test_d23_zip_and_tar_extractions_of_one_tree_derive_the_same_id` |
| D24 | canonical ZIP builds are byte-identical | `test_d24_canonical_zip_builds_are_byte_identical` |
| D25 | the archive SHA-256 stays distinct from `bundle-…` | `test_d25_the_archive_sha256_stays_distinct_from_the_bundle` |
| D26 | the packet SHA-256 is never interpreted as a bundle | `test_d26_the_packet_sha256_is_never_interpreted_as_a_bundle` |
| D27 | evaluation and batch reports gain no bundle identity | `test_d27_evaluation_and_batch_reports_gain_no_bundle_identity` |
| D28 | presentation-only edits move no `episode-…` | `test_d28_presentation_only_edits_move_no_episode_identity` |
| D29 | the serial-batch and comparison batteries remain green | `test_d29_the_serial_batch_and_comparison_batteries_remain_green` |
| D30 | the CLI surface is complete and typed | `test_d30_the_cli_surface_is_complete_and_typed` |

## 4. A latent defect this slice closed

`pack` wrote every member at the process umask default. The Residency snapshot
seals **raw four-digit `file_modes`** into `snap-…`, so a package shipping an
executable subject file would have failed to reopen itself — `pack` would have
recomputed a snapshot identity that disagreed with the one it had just written.

It stayed invisible only because every scaffolded template happens to be `0644`,
so the umask default coincided with the sealed mode. Closed by carrying a
`modes` map through `_read_member` → `_stage` → `os.chmod`, so members are
written at their **exact** source mode.

## 5. Two defects the gates caught in the battery itself

Both were found after D1–D30 was already green, by the acceptance gates rather
than by the laws. Recording them because in both cases the *test* was wrong in a
way that made the code look better than it was.

**G5 — the battery crashed with no engine.** `run_battery` under a genuinely
isolated extraction has no TRVM checkout above it, and every `trvs pack` needs
the engine (the `residency-repair` template pins an `identity_policy` on a `.wrl`
world). The battery raised `ENGINE_UNAVAILABLE` out of the fixture instead of
skipping. Its own docstring claimed "the Residency substrate needs no engine, so
nearly every law here runs everywhere" — which was simply false.

Fixed in the direction that keeps the most coverage rather than the least: the
manifest, closure and transport laws (D2, D3, D14–D16, D18, D22–D26, D30) now run
against a **synthetic package tree** that no substrate produced, so they need no
engine at all. That is the honest home for them — those are exactly the laws a
*recipient* relies on, and a recipient verifying a downloaded package usually has
no Forge checkout. Only the genuinely environment-shaped laws skip. Engine
absent: **12 passed, 18 skipped, 0 failed**. Engine present: **30/30**.

**D22's own fixture dropped the executable bit.** The synthetic tree added a
`0755` member — the packed fixture has none, every scaffolded file being `0644` —
and immediately failed `BUNDLE_MEMBER_MODE`. The helper that builds a
"non-canonical" ZIP was hardcoding `0o644` into `external_attr`.

That refusal was **correct**: mode is identity-bearing, so an archive that drops
the executable bit is carrying different content and must fail. The bug was the
fixture claiming to vary only transport while actually mutating the package. It
now carries each member's declared canonical mode, so D22 varies order,
compression, timestamps and comments — and nothing else, which is the claim it
was always supposed to be making.

## 6. Known caveat — canonical mode vs sealed mode

> **Closed.** The fifth ruling's reviewer ruled this caveat a reachable
> correctness defect rather than an acceptable residual, and it was closed in
> the following slice exactly as §6 predicted it would have to be: `pack` now
> refuses to seal a subject with a non-canonical mode at authoring time. See
> `TRAAVIIS_PORTABLE_MODE_MEMO.md` and RFC §5c. The rest of this section is kept
> as written, because it is the analysis the fix was built from.


The mode question has two correct answers and this slice ships both, on purpose:

- **`bundle-…` carries the canonical mode** (`0644`/`0755` — only the executable
  bit). It must, or a ZIP and tar round trip of one package would disagree, and
  group/other/umask noise would count as a package change.
- **`pack` writes the exact source mode.** It must, or `snap-…` would not
  reproduce.

The residual: a canonical archive normalizes e.g. `0664` → `0644`. A substrate
that seals raw modes would notice that, so a package whose subject files carry
non-canonical modes can round-trip through an archive and fail its *substrate*
reopen while passing its *bundle* reopen. Recording this rather than papering
over it. It cannot be fixed by loosening either identity — the honest fix, if it
ever bites, is for `pack` to refuse to seal a subject with a non-canonical mode
at authoring time, which is a new admission law and needs a ruling.

## 7. Autonomous decisions (flagged for review)

1. **`assets` added to the `distribution` block** alongside the ruled
   `entrypoint` / `documentation` / `screenshots`. Presentation-only like its
   siblings; excluded from `env-…` by the same allowlist.
2. **`TRAAVIIS_BUNDLE.json` skipped during tree scan**, so the manifest never
   appears in its own `members`. This is the ruled self-exclusion made
   operational.
3. **Symlinks refused for directories as well as files.** The ruling said
   symlinks are refused in v1; a symlinked *directory* is the same escape.
4. **`verify-bundle` accepts an archive** as well as a directory, extracting to
   a temp tree and running the identical verifier. Without it, D23's law is
   stated but not usable from the CLI.
5. **Verification moved before publication.** The previous pack order published,
   verified, then `rmtree`d on failure. The ruled order verifies the staged
   sibling, so a failed pack never occupies the destination even briefly. D20
   asserts no `.trvs-pack-` leftovers.
6. **`D27` tests bundle *identity*, not the word "bundle".** `EvaluationV1` has
   carried a field literally named `bundle` since the Episode Evidence Closure;
   it names the `episode-…` evidence directory an episode was kept in. Renaming
   it would break `batch.py` and the B-battery for no identity reason, and the
   ruling itself accepts the two senses coexisting (it froze
   `TRAAVIIS_BUNDLE.json` as distinct from `bundle.json`). So D27 asserts no
   `bundle_id` key and no `bundle-…` value anywhere, **and** pins the collision
   shut from the other side by requiring every surviving `bundle` field to hold
   an `episode-…` directory — which is what stops the old field from ever being
   read as the new identity.
7. **The structural laws were moved onto a synthetic tree** rather than made to
   skip, so a recipient's laws keep running on a recipient's machine. See §5.

## 8. Not started (deferred by ruling)

Batch-evidence distribution identity (needs a separate ruling), `eval-…`,
`agent-…`, ORS, `trvs serve --ors/--mcp`, MCP, the REPL.
