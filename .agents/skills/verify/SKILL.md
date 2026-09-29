---
name: verify
description: Drive linkedin-mdp-mcp through its real Streamable HTTP MCP surface, validate the five read-only LinkedIn MDP tools, and capture privacy-safe proof. Use after client, server, pagination, tool, or live E2E changes and before declaring a PR verified.
---

# Verify linkedin-mdp-mcp

The primary user surface is the Streamable HTTP MCP service. The direct Python client and linkedin-mdp-smoke command are useful diagnostics, but MCP behavior is proven through the MCP endpoint.

Never print the LinkedIn access token or raw member rows. Evidence is counts, metadata, tool names, page counts, truncation flags, and verifier status.

## Launch

Prerequisites are uv plus LINKEDIN_ACCESS_TOKEN or LINKEDIN_TOKEN in the environment. LINKEDIN_API_VERSION defaults to 202312.

For the normal isolated run, use the helper's all command:

~~~bash
.agents/skills/verify/scripts/verify.sh all
~~~

It runs uv sync --extra dev, allocates a free loopback port, starts exactly one server process, records its PID, and waits for the port to accept connections.

For a staged run:

~~~bash
state="$(mktemp -d "${TMPDIR:-/tmp}/linkedin-mdp-verify.XXXXXX")"
.agents/skills/verify/scripts/verify.sh launch "$state"
~~~

Each state directory gets its own port, so concurrent verification runs can coexist. Do not point the helper at an existing user-managed MCP process.

## Doctor

Run this after launch and whenever the instance looks wrong:

~~~bash
.agents/skills/verify/scripts/verify.sh doctor "$state"
~~~

Doctor checks the recorded PID, exact five-tool MCP surface, and a real linkedin_authorization_status call. It reports only status and tool count.

## Drive

Exercise the real MCP surface:

~~~bash
.agents/skills/verify/scripts/verify.sh drive "$state"
~~~

The drive runs tests/e2e_live.py against the launched endpoint. That script calls all five public tools and requires CONNECTIONS to exercise multiple pages. Its row oracle uses type-sensitive structural JSON equality, independent from the production json.dumps dedupe key, and first proves that the old single-snapshot behavior is rejected.

Use the feature map under features/ when a task concerns one tool. The full drive is intentionally small enough to run for every feature.

## Evidence

The helper copies the privacy-safe summary to a sibling evidence directory:

~~~text
<state>.evidence/e2e-summary.json
~~~

The summary records tool names, counts, pagination, truncation, and whether the independent oracle self-check passed. It never stores raw LinkedIn rows or the token.

A valid proof exercises the MCP endpoint, captures the action and resulting metadata, and verifies pagination behavior when CONNECTIONS is involved. Internal client-only calls are diagnostics, not final proof.

## Cleanup

Stop only the PID created by the helper:

~~~bash
.agents/skills/verify/scripts/verify.sh cleanup "$state"
~~~

Cleanup removes the helper's PID, port, and server log state. It leaves <state>.evidence untouched. If a staged run fails, run cleanup before retrying.

## Helpers

.agents/skills/verify/scripts/verify.sh is the supported harness.

~~~text
verify.sh all
verify.sh launch <state-dir>
verify.sh doctor <state-dir>
verify.sh drive <state-dir>
verify.sh cleanup <state-dir>
~~~

Use all for the usual proof. Use staged commands when debugging one phase. Keep /maintain-verification-skill in mind when the MCP tool surface or verification workflow changes.
