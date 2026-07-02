from regulon.core.hashing import GENESIS_HASH, canonical_json, chain_hash, hash_json, sha256_hex


def test_sha256_known_vector():
    # SHA-256 of empty input is a published constant.
    assert sha256_hex(b"") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def test_sha256_str_and_bytes_agree():
    assert sha256_hex("regulon") == sha256_hex(b"regulon")


def test_canonical_json_key_order_invariant():
    assert canonical_json({"b": 1, "a": [2, 3]}) == canonical_json({"a": [2, 3], "b": 1})
    assert canonical_json({"a": 1, "b": 2}) == '{"a":1,"b":2}'


def test_hash_json_stable_across_ordering():
    assert hash_json({"x": 1, "y": {"b": 2, "a": 3}}) == hash_json({"y": {"a": 3, "b": 2}, "x": 1})


def test_chain_hash_links_records():
    first = chain_hash(GENESIS_HASH, {"action": "ingest"})
    second = chain_hash(first, {"action": "approve"})
    assert first != second
    # Tampering with the first payload changes the recomputed chain.
    assert chain_hash(GENESIS_HASH, {"action": "ingest2"}) != first
    # Deterministic: same inputs, same hash.
    assert chain_hash(first, {"action": "approve"}) == second


def test_genesis_hash_is_fixed():
    assert len(GENESIS_HASH) == 64
    assert sha256_hex(b"regulon-audit-genesis") == GENESIS_HASH
