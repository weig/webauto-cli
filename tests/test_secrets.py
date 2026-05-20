"""Unit tests for symmetric encryption of dotenv files."""

from __future__ import annotations

from pathlib import Path

import pytest

from webauto._secrets import (
    KeyError_,
    decrypt_bytes,
    decrypt_file,
    encrypt_bytes,
    encrypt_file,
    ensure_key,
    generate_key,
    looks_encrypted,
    read_key,
    resolve_key_path,
)


@pytest.fixture
def key_dir(tmp_path: Path, monkeypatch) -> Path:
    """Point WEBAUTO_KEY_PATH at a tmp_path-local key."""
    key_path = tmp_path / "private_key"
    monkeypatch.setenv("WEBAUTO_KEY_PATH", str(key_path))
    return key_path


class TestKeygen:
    def test_generates_at_default_path(self, key_dir: Path) -> None:
        path = generate_key()
        assert path == key_dir
        assert path.exists()
        assert (path.stat().st_mode & 0o777) == 0o600

    def test_refuses_to_overwrite(self, key_dir: Path) -> None:
        generate_key()
        with pytest.raises(KeyError_, match="already exists"):
            generate_key()

    def test_force_overwrite(self, key_dir: Path) -> None:
        generate_key()
        original = key_dir.read_bytes()
        generate_key(force=True)
        assert key_dir.read_bytes() != original


class TestReadKey:
    def test_missing_key_raises(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("WEBAUTO_KEY_PATH", str(tmp_path / "nope"))
        with pytest.raises(KeyError_, match="not found"):
            read_key()

    def test_invalid_key_raises(self, key_dir: Path) -> None:
        key_dir.parent.mkdir(parents=True, exist_ok=True)
        key_dir.write_bytes(b"not-a-fernet-key")
        with pytest.raises(KeyError_, match="not a valid Fernet key"):
            read_key()

    def test_empty_key_raises(self, key_dir: Path) -> None:
        key_dir.parent.mkdir(parents=True, exist_ok=True)
        key_dir.write_bytes(b"")
        with pytest.raises(KeyError_, match="empty"):
            read_key()

    def test_too_loose_perms_get_tightened(self, key_dir: Path) -> None:
        generate_key()
        key_dir.chmod(0o644)  # world-readable
        # read_key should silently tighten + warn.
        read_key()
        assert (key_dir.stat().st_mode & 0o777) == 0o600


class TestEncryptDecrypt:
    def test_roundtrip_bytes(self, key_dir: Path) -> None:
        generate_key()
        plain = b"hello world\nUSERNAME=alice\nPASSWORD=secret\n"
        token = encrypt_bytes(plain)
        assert plain not in token  # not just appended
        assert decrypt_bytes(token) == plain

    def test_roundtrip_file(self, tmp_path: Path, key_dir: Path) -> None:
        generate_key()
        plain_path = tmp_path / "plain.env"
        plain_path.write_text("URL=https://x\nPASSWORD=hunter2\n")
        enc_path = tmp_path / "enc.env"
        encrypt_file(plain_path, enc_path)

        # Encrypted file is chmod 600.
        assert (enc_path.stat().st_mode & 0o777) == 0o600
        # Content isn't readable as plaintext.
        assert b"hunter2" not in enc_path.read_bytes()
        # Decrypts correctly.
        assert decrypt_file(enc_path) == "URL=https://x\nPASSWORD=hunter2\n"

    def test_wrong_key_raises(self, tmp_path: Path, monkeypatch) -> None:
        # Encrypt with key A, try to decrypt with key B.
        key_a = tmp_path / "key_a"
        key_b = tmp_path / "key_b"
        monkeypatch.setenv("WEBAUTO_KEY_PATH", str(key_a))
        generate_key()
        token = encrypt_bytes(b"secret")

        monkeypatch.setenv("WEBAUTO_KEY_PATH", str(key_b))
        generate_key()
        with pytest.raises(KeyError_, match="invalid token or wrong key"):
            enc_path = tmp_path / "enc.bin"
            enc_path.write_bytes(token)
            decrypt_file(enc_path)


class TestLooksEncrypted:
    def test_fernet_token_detected(self, key_dir: Path) -> None:
        generate_key()
        token = encrypt_bytes(b"x")
        assert looks_encrypted(token)

    def test_plaintext_dotenv_not_detected(self) -> None:
        assert not looks_encrypted(b"URL=https://x\nUSERNAME=alice\n")

    def test_empty_not_detected(self) -> None:
        assert not looks_encrypted(b"")
        assert not looks_encrypted(b"\n\n  \n")


class TestEnsureKey:
    def test_generates_when_missing(self, key_dir: Path) -> None:
        assert not key_dir.exists()
        path = ensure_key()
        assert path == key_dir
        assert key_dir.exists()

    def test_no_op_when_exists(self, key_dir: Path) -> None:
        generate_key()
        original = key_dir.read_bytes()
        ensure_key()
        assert key_dir.read_bytes() == original


class TestEncryptAutoKey:
    def test_encrypt_bytes_auto_generates_key(self, key_dir: Path) -> None:
        """encrypt_bytes should not require a pre-existing key."""
        assert not key_dir.exists()
        token = encrypt_bytes(b"hello")
        assert key_dir.exists()
        assert decrypt_bytes(token) == b"hello"

    def test_encrypt_file_auto_generates_key(self, tmp_path: Path, key_dir: Path) -> None:
        plain = tmp_path / "plain.env"
        plain.write_text("PASSWORD=x\n")
        enc = tmp_path / "enc.env"
        assert not key_dir.exists()
        encrypt_file(plain, enc)
        assert key_dir.exists()
        assert decrypt_file(enc) == "PASSWORD=x\n"


class TestDecryptDoesNotAutoGen:
    def test_decrypt_fails_when_key_missing(self, tmp_path: Path, key_dir: Path) -> None:
        # User has an encrypted file but no key (e.g., moved / deleted).
        # decrypt MUST fail loud — silent key-gen would just produce
        # InvalidToken errors that look like file corruption.
        # Set up: generate key, encrypt, then remove key.
        generate_key()
        plain = tmp_path / "plain.env"
        plain.write_text("PASSWORD=secret\n")
        enc = tmp_path / "enc.env"
        encrypt_file(plain, enc)
        key_dir.unlink()
        with pytest.raises(KeyError_, match="not found"):
            decrypt_file(enc)


class TestPathResolution:
    def test_explicit_wins(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("WEBAUTO_KEY_PATH", str(tmp_path / "from_env"))
        explicit = tmp_path / "explicit"
        assert resolve_key_path(explicit) == explicit

    def test_env_overrides_default(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("WEBAUTO_KEY_PATH", str(tmp_path / "from_env"))
        assert resolve_key_path() == tmp_path / "from_env"

    def test_default_path_under_xdg(self, monkeypatch, tmp_path: Path) -> None:
        monkeypatch.delenv("WEBAUTO_KEY_PATH", raising=False)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
        assert resolve_key_path() == tmp_path / "cfg" / "webauto-cli" / "private_key"
