"""Symmetric encryption of dotenv files.

Threat model: prevent casual file leaks where someone copying just the env
file would learn the credentials. Two artifacts in separate places:

    ~/.config/webauto-cli/private_key      ← the Fernet key (32 bytes, b64)
    <user-chosen path>                      ← the encrypted dotenv file

Both files use chmod 600. Either file alone is useless.

Crypto: Fernet from `cryptography` (AES-128-CBC + HMAC-SHA256, AEAD).
Standard, well-reviewed; not a place to be clever.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken


# ----------------------------------------------------------------------------
# Paths
# ----------------------------------------------------------------------------


def _config_home() -> Path:
    """`$XDG_CONFIG_HOME` or `~/.config`."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg)
    return Path.home() / ".config"


def default_key_path() -> Path:
    """`~/.config/webauto-cli/private_key` (XDG-respecting)."""
    return _config_home() / "webauto-cli" / "private_key"


def resolve_key_path(explicit: Optional[Path] = None) -> Path:
    """Pick the key path. Precedence: explicit arg → `WEBAUTO_KEY_PATH` env → default."""
    if explicit is not None:
        return Path(explicit).expanduser()
    env_override = os.environ.get("WEBAUTO_KEY_PATH")
    if env_override:
        return Path(env_override).expanduser()
    return default_key_path()


# ----------------------------------------------------------------------------
# Key management
# ----------------------------------------------------------------------------


class KeyError_(RuntimeError):
    """Raised on key-file read/write failures with actionable context."""


def generate_key(path: Optional[Path] = None, *, force: bool = False) -> Path:
    """Generate a fresh Fernet key and write it to `path` with chmod 600.

    Refuses to overwrite an existing file unless `force=True` — accidentally
    regenerating the key would orphan every existing encrypted env file.
    """
    target = resolve_key_path(path)
    if target.exists() and not force:
        raise KeyError_(
            f"key file already exists at {target}; pass force=True to overwrite "
            f"(this will make existing encrypted env files unreadable)"
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        target.parent.chmod(0o700)
    except OSError:
        pass

    key = Fernet.generate_key()
    target.write_bytes(key)
    target.chmod(0o600)
    return target


def ensure_key(path: Optional[Path] = None) -> Path:
    """Return the resolved key path. Generate one if it doesn't exist yet.

    Used for first-use ergonomics: `encrypt` and `--env` setups should work
    without making the user run `keygen` explicitly. **Never** call this from
    a decrypt path — generating a new key would silently break decryption.
    """
    target = resolve_key_path(path)
    if not target.exists():
        import sys
        print(
            f"[webauto-cli secrets] no key at {target}; generating a fresh one",
            file=sys.stderr,
        )
        generate_key(target)
    return target


def read_key(path: Optional[Path] = None) -> bytes:
    """Read the Fernet key, validating perms and shape."""
    target = resolve_key_path(path)
    if not target.exists():
        raise KeyError_(
            f"key file not found at {target}. Generate one with "
            f"`webauto-cli secrets keygen`."
        )

    # Warn if file is world-readable; it's a key, not a config.
    try:
        mode = target.stat().st_mode & 0o777
        if mode & (stat.S_IRGRP | stat.S_IROTH):
            # Don't fail hard — but the user should know.
            import sys
            print(
                f"[webauto-cli] warning: key file {target} has permissions "
                f"{oct(mode)}; expected 0o600. Tightening.",
                file=sys.stderr,
            )
            try:
                target.chmod(0o600)
            except OSError:
                pass
    except OSError:
        pass

    raw = target.read_bytes().strip()
    if not raw:
        raise KeyError_(f"key file {target} is empty")
    # Validate it's a real Fernet key — constructing the cipher object parses
    # and verifies the format.
    try:
        Fernet(raw)
    except (ValueError, TypeError) as e:
        raise KeyError_(
            f"key file {target} is not a valid Fernet key: {e}"
        ) from e
    return raw


# ----------------------------------------------------------------------------
# Encrypt / decrypt
# ----------------------------------------------------------------------------


def encrypt_bytes(plaintext: bytes, *, key_path: Optional[Path] = None) -> bytes:
    """Encrypt `plaintext` to a Fernet token (URL-safe base64 bytes).

    Auto-generates the key file if it doesn't exist yet — encrypt is the
    natural first-use moment, so this avoids forcing a separate `keygen` step.
    """
    ensure_key(key_path)
    key = read_key(key_path)
    return Fernet(key).encrypt(plaintext)


def decrypt_bytes(token: bytes, *, key_path: Optional[Path] = None) -> bytes:
    """Decrypt a Fernet token. Raises `InvalidToken` if ciphertext / key mismatch."""
    key = read_key(key_path)
    return Fernet(key).decrypt(token)


def looks_encrypted(content: bytes) -> bool:
    """Heuristic: does this look like a Fernet token?

    Fernet tokens are URL-safe base64 strings starting with `gAAAAA` (the
    version byte 0x80 + a timestamp + ...). We're tolerant of leading
    whitespace and a trailing newline.
    """
    stripped = content.strip()
    if not stripped:
        return False
    return stripped.startswith(b"gAAAAA")


def encrypt_file(
    plain_path: Path,
    enc_path: Path,
    *,
    key_path: Optional[Path] = None,
) -> None:
    """Encrypt `plain_path` → write Fernet token to `enc_path` (chmod 600)."""
    plain_path = Path(plain_path).expanduser()
    enc_path = Path(enc_path).expanduser()
    plaintext = plain_path.read_bytes()
    token = encrypt_bytes(plaintext, key_path=key_path)
    enc_path.parent.mkdir(parents=True, exist_ok=True)
    enc_path.write_bytes(token)
    try:
        enc_path.chmod(0o600)
    except OSError:
        pass


def decrypt_file(enc_path: Path, *, key_path: Optional[Path] = None) -> str:
    """Decrypt `enc_path` and return the plaintext as a string."""
    enc_path = Path(enc_path).expanduser()
    token = enc_path.read_bytes().strip()
    try:
        plain = decrypt_bytes(token, key_path=key_path)
    except InvalidToken as e:
        raise KeyError_(
            f"failed to decrypt {enc_path}: invalid token or wrong key. "
            f"Verify the key at {resolve_key_path(key_path)} matches the one "
            f"used to encrypt this file."
        ) from e
    return plain.decode("utf-8")
