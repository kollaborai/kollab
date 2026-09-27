"""Run bidirectional Ed25519/JCS vectors between Python and Node.js."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from nacl.signing import SigningKey

from plugins.hub.dns.a2a_signing import sign_agent_card, verify_agent_card

REPO_ROOT = Path(__file__).resolve().parents[3]
NODE_SCRIPT = Path(__file__).with_name("verify_vectors.mjs")
ORIGIN = "https://agents.example"
SEED = bytes(range(32))


def _vector_card() -> dict:
    # Exercise JCS UTF-16 ordering, Unicode scalar values, and number forms.
    return {
        "name": "Café 🌲 東京",
        "version": "1.0.0",
        "description": "Unicode: \ue000, 𐀀, 😀",
        "supportedInterfaces": [
            {
                "url": ORIGIN + "/a2a",
                "protocolBinding": "JSONRPC",
                "protocolVersion": "1.0",
            }
        ],
        "x-kollab-numbers": {
            "integer": 9007199254740991,
            "negativeInteger": -9007199254740991,
            "decimal": 333333333.3333333,
            "negativeZero": -0.0,
            "tiny": 1e-7,
        },
        "x-kollab-key-order": {"z": 1, "é": 2, "😀": 3, "a": 4, "𐀀": 5},
    }


def main() -> int:
    node = shutil.which("node")
    if node is None:
        raise SystemExit("node executable is required for the cross-language vector")
    key = SigningKey(SEED)
    python_signed = sign_agent_card(_vector_card(), key)
    with tempfile.TemporaryDirectory(prefix="kollab-a2a-signing-") as temp_dir:
        temp = Path(temp_dir)
        python_path = temp / "python-signed-card.json"
        node_path = temp / "node-signed-card.json"
        python_path.write_text(
            json.dumps(python_signed, ensure_ascii=False), encoding="utf-8"
        )
        result = subprocess.run(
            [node, str(NODE_SCRIPT), str(python_path), str(node_path)],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        node_signed = json.loads(node_path.read_text(encoding="utf-8"))
        verified = verify_agent_card(
            node_signed, origin=ORIGIN, pinned_public_key=key.verify_key.encode()
        )
        if verified.public_key_hex != key.verify_key.encode().hex():
            raise AssertionError(
                "Python accepted Node vector under an unexpected pinned key"
            )
        print(result.stdout.strip())
        print(
            "Python verified Node.js EdDSA signature using the same RFC 8785 payload."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
