from warden_core.hashing import sha256_hex


def test_known_value():
    assert sha256_hex(b"") == (
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    )


def test_deterministic():
    assert sha256_hex(b"warden") == sha256_hex(b"warden")