from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
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


def _date_key(row: dict) -> float:
    value = row.get("DATE")
    if isinstance(value, (int, float)):
        x = float(value)
        return x / 1000.0 if x > 10_000_000_000 else x
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit():
            x = float(text)
            return x / 1000.0 if x > 10_000_000_000 else x
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except ValueError:
            return 0.0
    return 0.0


async def main() -> None:
    endpoint = os.getenv("MCP_ENDPOINT", "http://127.0.0.1:8000/mcp")
    output_dir = Path(os.getenv("DM_OUTPUT_DIR", "dm-artifact"))
    output_dir.mkdir(parents=True, exist_ok=True)

    async with Client(endpoint) as client:
        result = await asyncio.wait_for(
            client.call_tool("linkedin_inbox", {"max_pages": 10}),
            timeout=120.0,
        )
        inbox = _structured(result)

    rows = inbox.get("rows", [])
    if not isinstance(rows, list):
        raise RuntimeError("linkedin_inbox did not return a rows list")

    messages = [row for row in rows if isinstance(row, dict)]
    messages.sort(key=_date_key, reverse=True)
    latest = messages[:10]

    plaintext = json.dumps(
        {
            "count": len(latest),
            "messages": latest,
        },
        ensure_ascii=False,
        indent=2,
    ).encode("utf-8")

    public_key = serialization.load_pem_public_key(
        Path(os.environ["DM_PUBLIC_KEY_PATH"]).read_bytes()
    )
    aes_key = AESGCM.generate_key(bit_length=256)
    nonce = os.urandom(12)
    ciphertext = AESGCM(aes_key).encrypt(nonce, plaintext, b"linkedin-mdp-latest-dms-v1")
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
    print(f"encrypted_messages={len(latest)}")


if __name__ == "__main__":
    asyncio.run(main())
