#!/usr/bin/env node
import { main } from '../src/index.mjs';

main()
  .then((code) => process.exit(code ?? 0))
  .catch((err) => {
    process.stderr.write(`traaviis: ${err.stack || err.message}\n`);
    process.exit(1);
  });
