#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
command_name="${1:-all}"
state_dir="${2:-}"

require_state() {
  if [[ -z "$state_dir" ]]; then
    echo "state directory is required for $command_name" >&2
    exit 2
  fi
}

require_token() {
  if [[ -z "${LINKEDIN_ACCESS_TOKEN:-}" && -z "${LINKEDIN_TOKEN:-}" ]]; then
    echo "missing LINKEDIN_ACCESS_TOKEN or LINKEDIN_TOKEN" >&2
    exit 2
  fi
}

launch() {
  require_state
  mkdir -p "$state_dir"
  if [[ -f "$state_dir/server.pid" ]] && kill -0 "$(cat "$state_dir/server.pid")" 2>/dev/null; then
    echo "verification server already running for state: $state_dir" >&2
    exit 2
  fi

  cd "$repo_root"
  uv sync --extra dev
  local port
  port="${MCP_PORT:-$(uv run python - <<'PY'
import socket

with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    print(sock.getsockname()[1])
PY
)}"
  printf '%s\n' "$port" > "$state_dir/port"

  MCP_HOST=127.0.0.1 MCP_PORT="$port" uv run linkedin-mdp-mcp >"$state_dir/server.log" 2>&1 &
  local pid=$!
  printf '%s\n' "$pid" > "$state_dir/server.pid"

  for _ in {1..30}; do
    if (echo > /dev/tcp/127.0.0.1/"$port") 2>/dev/null; then
      echo "launched pid=$pid port=$port"
      return
    fi
    sleep 1
  done

  cat "$state_dir/server.log" >&2
  echo "MCP server did not become ready" >&2
  exit 1
}

doctor() {
  require_state
  require_token
  local pid port
  pid="$(cat "$state_dir/server.pid")"
  port="$(cat "$state_dir/port")"
  if ! kill -0 "$pid" 2>/dev/null; then
    echo "recorded verification server is not running" >&2
    exit 1
  fi

  cd "$repo_root"
  MCP_ENDPOINT="http://127.0.0.1:$port/mcp" uv run python - <<'PY'
import asyncio
import os

from mcp import Client

EXPECTED = {
    "linkedin_authorization_status",
    "linkedin_connections",
    "linkedin_invitations",
    "linkedin_inbox",
    "linkedin_member_changelog",
}


async def main() -> None:
    """Check the live MCP surface and authorization without printing member data."""
    async with Client(os.environ["MCP_ENDPOINT"]) as client:
        listed = await client.list_tools()
        names = {tool.name for tool in listed.tools}
        if names != EXPECTED:
            raise SystemExit(f"unexpected MCP tools: {sorted(names)}")
        result = await client.call_tool("linkedin_authorization_status", {})
        if result.is_error:
            raise SystemExit("authorization status call failed")
        print(f"doctor_ok tools={len(names)} authorization=ok")


asyncio.run(main())
PY
}

drive() {
  require_state
  local port evidence_dir
  port="$(cat "$state_dir/port")"
  evidence_dir="$state_dir.evidence"
  mkdir -p "$evidence_dir"

  cd "$repo_root"
  rm -f e2e-summary.json
  MCP_ENDPOINT="http://127.0.0.1:$port/mcp" uv run python tests/e2e_live.py
  cp e2e-summary.json "$evidence_dir/e2e-summary.json"
  rm -f e2e-summary.json
  echo "evidence=$evidence_dir/e2e-summary.json"
}

cleanup() {
  require_state
  if [[ -f "$state_dir/server.pid" ]]; then
    local pid
    pid="$(cat "$state_dir/server.pid")"
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid"
      for _ in {1..50}; do
        if ! kill -0 "$pid" 2>/dev/null; then
          break
        fi
        sleep 0.1
      done
      if kill -0 "$pid" 2>/dev/null; then
        kill -KILL "$pid"
      fi
    fi
  fi

  rm -f "$state_dir/server.pid" "$state_dir/port" "$state_dir/server.log"
  rmdir "$state_dir" 2>/dev/null || true
  echo "cleanup_ok evidence_preserved=$state_dir.evidence"
}

run_all() {
  require_token
  state_dir="$(mktemp -d "${TMPDIR:-/tmp}/linkedin-mdp-verify.XXXXXX")"
  local evidence_dir="$state_dir.evidence"
  trap 'cleanup >/dev/null 2>&1 || true' EXIT
  launch
  doctor
  drive
  cleanup
  trap - EXIT
  test -f "$evidence_dir/e2e-summary.json"
  echo "verification_ok evidence_dir=$evidence_dir"
}

case "$command_name" in
  all)
    run_all
    ;;
  launch)
    launch
    ;;
  doctor)
    doctor
    ;;
  drive)
    drive
    ;;
  cleanup)
    cleanup
    ;;
  *)
    echo "usage: $0 {all|launch|doctor|drive|cleanup} [state-dir]" >&2
    exit 2
    ;;
esac
