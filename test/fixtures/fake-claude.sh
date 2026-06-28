#!/bin/sh
# Hermetic stand-in for the `claude` CLI used by provider.claude_code tests.
# Emits the same --output-format json shape the real CLI does (result + cost),
# ignoring its args so the test is deterministic.
echo '{"result":"hello from fake claude","total_cost_usd":0.0012,"session_id":"sess-fake-1","model":"claude-haiku","duration_ms":42}'
