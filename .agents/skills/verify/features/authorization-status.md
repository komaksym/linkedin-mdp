# Authorization status

## Sub-features

- Exact public tool name: linkedin_authorization_status.
- Read-only Member Data Portability authorization response.
- Token stays in the server environment.

## How to get to it (user POV)

Connect an MCP client to the running linkedin-mdp-mcp endpoint and call linkedin_authorization_status with no arguments.

## Driving it with verify.sh

Run the project verify skill. Doctor calls linkedin_authorization_status through MCP before the full E2E drive runs. Evidence proves the call succeeded without storing the authorization payload.

## Gotchas

A missing or expired token makes this fail before feature verification is meaningful. Never print the token or add it to tool arguments.
