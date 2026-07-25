# Legacy Node harness (quarantined)

This is the original TRAAVIIS: a minimal, pluggable terminal harness for the
[&] Protocol stack, written in Node ESM. It is kept here for history. It is
**not** the product, **not** shipped in the release packet, and **not** part of
the acceptance battery.

## Why it moved

The repository claims one authoritative package: the Python `traaviis`
distribution declared in `pyproject.toml`, which installs the `trvs` command.
While the Node harness sat at the repository root it contradicted that claim in
three concrete ways.

1. **Two packages, one name.** `package.json` declared a `traaviis` bin and
   `pyproject.toml` declares a `traaviis` console script. Installing both put two
   different programs behind one command.
2. **The declared test command was red.** `npm test` ran
   `test/harness.test.mjs`, which resolves a hard-coded absolute path to a
   sibling checkout:

   ```js
   const SIBLING_GOVERN =
     '/home/travis/ProjectAmp2/AmpersandBoxDesign/box-and-box/bin/govern.mjs';
   ```

   From a clean extraction of the packet that path does not exist, so the one
   test command the packet advertised failed on arrival. A release packet whose
   own documented entry point is red is not standalone.
3. **The description was stale.** `package.json` still described the repository
   as "a minimal terminal harness", which stopped being true when TRAAVIIS
   became a toolchain for evidence-grade agent evaluation.

Quarantine fixes all three without deleting work: the harness is preserved, the
name collision is gone (this package is `private` and its bin is renamed), and
the packet builder excludes `legacy/` outright, so nothing here can affect
packet identity or the acceptance gate.

## Running it

It still runs, on the same terms it always did — it is simply no longer
supported, no longer released, and no longer gated.

```
cd legacy/node-harness
node bin/traaviis.mjs
node test/harness.test.mjs   # requires a sibling box-and-box checkout
```

## Known non-standalone dependencies

- `SIBLING_GOVERN` in `test/harness.test.mjs` — an absolute path to
  `AmpersandBoxDesign/box-and-box/bin/govern.mjs`. This is why the battery is
  not portable.
- `test/harness.test.mjs` resolves `fake-claude.sh` out of the live tree at
  `../../../test/fixtures/fake-claude.sh`. That fixture is owned by the Python
  battery; this tree borrows it rather than keeping a second copy that could
  drift. The dependency points legacy → live and never the reverse.
