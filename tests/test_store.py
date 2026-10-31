import json
import sys

import pytest

from vulturif_kit import securestore
from vulturif_kit.credentials import CredentialStore
from vulturif_kit.securestore import SecureStore

FIELDS = (("API_KEY", "API key", "Required.", True), ("USER_ID", "User ID", "", False))


def test_round_trip_and_not_plain(tmp_path):
    store = SecureStore(b"Demo/securestore/1")
    store.write(tmp_path / "x.dat", {"paths": ["C:/secret"]})
    raw = (tmp_path / "x.dat").read_bytes()
    assert b"secret" not in raw
    assert store.read(tmp_path / "x.dat") == {"paths": ["C:/secret"]}


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI")
def test_entropy_is_part_of_the_key(tmp_path):
    SecureStore(b"one").write(tmp_path / "x.dat", [1])
    assert SecureStore(b"two").read(tmp_path / "x.dat") is None
    assert SecureStore(None).read(tmp_path / "x.dat") is None


def test_legacy_json_is_migrated(tmp_path):
    legacy = tmp_path / "old.json"
    legacy.write_text(json.dumps([1, 2]))
    store = SecureStore(b"e")
    assert store.read(tmp_path / "new.dat", legacy=legacy) == [1, 2]
    assert not legacy.exists()
    assert store.read(tmp_path / "new.dat") == [1, 2]


def test_garbage_reads_as_nothing(tmp_path):
    (tmp_path / "x.dat").write_bytes(b"not ours")
    assert SecureStore(b"e").read(tmp_path / "x.dat") is None


def test_env_only_seeds_the_first_run(tmp_path):
    creds = CredentialStore(tmp_path / "credentials.dat", FIELDS)
    env = {"API_KEY": "from-env"}
    assert not creds.exists()
    assert creds.resolve(env) == {"API_KEY": "from-env", "USER_ID": ""}
    creds.save_stored({"API_KEY": ""})      # cleared in the settings screen
    assert creds.resolve(env) == {"API_KEY": "", "USER_ID": ""}


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI")
def test_reads_leashwares_pre_kit_file(tmp_path):
    # api/credentials.py wrote a bare DPAPI blob, no magic line, no entropy.
    path = tmp_path / "credentials.dat"
    path.write_bytes(securestore.protect(json.dumps({"API_KEY": "k"}).encode()))
    creds = CredentialStore(path, FIELDS)
    assert creds.resolve({}) == {"API_KEY": "k", "USER_ID": ""}
    creds.save_stored(creds.resolve({}))
    assert path.read_bytes().startswith(securestore.MAGIC_DPAPI)
    assert creds.resolve({})["API_KEY"] == "k"
