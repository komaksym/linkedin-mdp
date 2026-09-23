from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from mcp import Client


def _structured(result):
    if result.is_error:
        texts = []
        for block in result.content:
            text = getattr(block, "text", None)
            if text:
                texts.append(text)
        raise RuntimeError("MCP tool failed: " + " | ".join(texts))
    return result.structured_content or {}


async def _call(client: Client, name: str, args: dict, timeout: float = 180.0):
    result = await asyncio.wait_for(client.call_tool(name, args), timeout=timeout)
    return _structured(result)


async def main() -> None:
    endpoint = os.getenv("MCP_ENDPOINT", "http://127.0.0.1:8000/mcp")
    output_dir = Path(os.getenv("SYNC_OUTPUT_DIR", "connection-sync-artifact"))
    output_dir.mkdir(parents=True, exist_ok=True)

    async with Client(endpoint) as client:
        changelog = await _call(
            client,
            "linkedin_member_changelog",
            {"count": 50, "max_pages": 5},
        )
        connections = await _call(
            client,
            "linkedin_connections",
            {"max_pages": 50},
        )

    connection_rows = connections.get("rows", [])
    change_events = changelog.get("events", [])
    if not isinstance(connection_rows, list):
        raise RuntimeError("linkedin_connections did not return a rows list")
    if not isinstance(change_events, list):
        raise RuntimeError("linkedin_member_changelog did not return an events list")

    payload = {
        "connections": connection_rows,
        "changelog": changelog,
        "connection_count": len(connection_rows),
        "changelog_event_count": len(change_events),
    }
    plaintext = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    public_key = serialization.load_pem_public_key(
        Path(os.environ["SYNC_PUBLIC_KEY_PATH"]).read_bytes()
    )
    aes_key = AESGCM.generate_key(bit_length=256)
    nonce = os.urandom(12)
    aad = b"linkedin-mdp-connection-sync-v1"
    ciphertext = AESGCM(aes_key).encrypt(nonce, plaintext, aad)
    wrapped_key = public_key.encrypt(
        aes_key,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )

    (output_dir / "ciphertext.bin").write_bytes(ciphertext)
    (output_dir / "nonce.bin").write_bytes(nonce)
    (output_dir / "wrapped_key.bin").write_bytes(wrapped_key)
    (output_dir / "meta.json").write_text(
        json.dumps({
            "connection_count": len(connection_rows),
            "changelog_event_count": len(change_events),
            "next_start_time": changelog.get("next_start_time"),
        }),
        encoding="utf-8",
    )
    print(f"connection_count={len(connection_rows)}")
    print(f"changelog_event_count={len(change_events)}")


if __name__ == "__main__":
    asyncio.run(main())
