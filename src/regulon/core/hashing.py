"""Hashing primitives: content hashes, canonical JSON, and the audit-chain link function.

The audit log (M6) is an append-only chain where each record commits to its predecessor's
hash; :func:`chain_hash` is that link function and is defined here so it has exactly one
implementation and one set of tests.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def sha256_hex(data: bytes | str) -> str:
    """Return the SHA-256 hex digest of raw bytes (strings are UTF-8 encoded)."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def canonical_json(obj: Any) -> str:
    """Serialize to deterministic JSON: sorted keys, compact separators, no ASCII escaping.

    Two structurally equal objects always produce byte-identical output, which makes the
    result safe to hash.
    """
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def hash_json(obj: Any) -> str:
    """Return the SHA-256 hex digest of an object's canonical JSON form."""
    return sha256_hex(canonical_json(obj))


GENESIS_HASH = sha256_hex(b"regulon-audit-genesis")


def chain_hash(prev_hash: str, payload: Any) -> str:
    """Return the hash linking an audit record to its predecessor.

    Args:
        prev_hash: Hex digest of the previous record (:data:`GENESIS_HASH` for the first).
        payload: JSON-serializable record content.
    """
    return sha256_hex(prev_hash + canonical_json(payload))
