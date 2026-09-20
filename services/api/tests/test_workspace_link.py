import warden_api
from warden_core.hashing import sha256_hex


def test_api_can_import_core():
    assert warden_api.__version__
    assert len(sha256_hex(b"x")) == 64