"""Phase 2 Step 2.1 — DB encryption key lifecycle."""

import pytest

from src.security import keyring_store
from src.security.errors import PreviousDataUnrecoverableError

pytestmark = pytest.mark.usefixtures("fake_keyring")


def test_generate_key_is_64_hex_chars_32_bytes():
    key = keyring_store.generate_key()
    assert len(key) == 64
    assert bytes.fromhex(key)  # valid hex
    assert len(bytes.fromhex(key)) == 32
    assert key != keyring_store.generate_key()  # random


def test_store_then_load_round_trip():
    assert keyring_store.load_key() is None
    key = keyring_store.generate_key()
    keyring_store.store_key(key)
    assert keyring_store.load_key() == key


def test_store_key_replaces_existing():
    keyring_store.store_key("a" * 64)
    keyring_store.store_key("b" * 64)
    assert keyring_store.load_key() == "b" * 64


def test_delete_key_is_idempotent():
    keyring_store.delete_key()  # nothing stored — must not raise
    keyring_store.store_key("c" * 64)
    keyring_store.delete_key()
    assert keyring_store.load_key() is None


def test_resolve_key_first_launch_generates_and_persists(tmp_path):
    db = tmp_path / "session.db"  # does not exist
    key = keyring_store.resolve_key(str(db))
    assert len(key) == 64
    assert keyring_store.load_key() == key  # persisted for next launch


def test_resolve_key_returns_stored_key_on_subsequent_launch(tmp_path):
    db = tmp_path / "session.db"
    first = keyring_store.resolve_key(str(db))
    db.write_bytes(b"encrypted-bytes")  # now the DB file exists
    assert keyring_store.resolve_key(str(db)) == first


def test_resolve_key_raises_unrecoverable_when_db_exists_but_no_key(tmp_path):
    db = tmp_path / "session.db"
    db.write_bytes(b"old encrypted database")
    with pytest.raises(PreviousDataUnrecoverableError) as excinfo:
        keyring_store.resolve_key(str(db))
    assert str(excinfo.value) == (
        "Your previous data is protected by your Windows account and cannot be "
        "recovered after a Windows reinstall. Starting fresh."
    )
    # the old file was not touched / opened
    assert db.read_bytes() == b"old encrypted database"
