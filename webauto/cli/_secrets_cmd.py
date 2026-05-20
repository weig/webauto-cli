"""`webauto-cli secrets` subcommand — keygen / encrypt / decrypt / path."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from webauto._secrets import (
    KeyError_,
    decrypt_file,
    default_key_path,
    encrypt_file,
    generate_key,
    resolve_key_path,
)


def _add_keygen(subparsers) -> None:
    p = subparsers.add_parser(
        "keygen",
        help="Generate a fresh Fernet key (refuses to overwrite by default).",
    )
    p.add_argument("--key-file", type=Path, default=None,
                   help=f"Path. Default: {default_key_path()}")
    p.add_argument("--force", action="store_true",
                   help="Overwrite existing key (DANGER: orphans every "
                        "encrypted env file made with the old key).")
    p.add_argument("--if-missing", action="store_true",
                   help="No-op if a key already exists (exit 0). Use this "
                        "in install scripts / idempotent setup paths.")


def _add_encrypt(subparsers) -> None:
    p = subparsers.add_parser(
        "encrypt",
        help="Encrypt a plaintext .env file → Fernet token.",
    )
    p.add_argument("input", type=Path, help="Plaintext .env file to encrypt.")
    p.add_argument("--out", type=Path, required=True,
                   help="Output path for the encrypted file.")
    p.add_argument("--key-file", type=Path, default=None,
                   help="Override the key path.")


def _add_decrypt(subparsers) -> None:
    p = subparsers.add_parser(
        "decrypt",
        help="Decrypt an encrypted .env to stdout (for inspection).",
    )
    p.add_argument("input", type=Path, help="Encrypted file to decrypt.")
    p.add_argument("--key-file", type=Path, default=None,
                   help="Override the key path.")


def _add_path(subparsers) -> None:
    p = subparsers.add_parser(
        "path",
        help="Print the resolved key path.",
    )
    p.add_argument("--key-file", type=Path, default=None,
                   help="Override the key path.")


def secrets_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="webauto-cli secrets",
        description="Manage the encryption key + encrypted .env files.",
    )
    sub = parser.add_subparsers(dest="op", required=True)
    _add_keygen(sub)
    _add_encrypt(sub)
    _add_decrypt(sub)
    _add_path(sub)
    args = parser.parse_args(argv)

    try:
        if args.op == "keygen":
            target = resolve_key_path(args.key_file)
            # --if-missing: silently no-op when a key is already present.
            if args.if_missing and target.exists():
                print(f"key already exists: {target}", file=sys.stderr)
                return 0
            # If a key already exists and the user hasn't said --force,
            # ask interactively before destroying it. Non-TTY shells (CI,
            # piped sh) refuse outright with a clear pointer.
            if target.exists() and not args.force:
                if not sys.stdin.isatty():
                    print(
                        f"[webauto-cli secrets] key already exists at {target}.\n"
                        f"  Refusing to overwrite from a non-interactive shell.\n"
                        f"  Pass --force to overwrite or --if-missing to no-op.",
                        file=sys.stderr,
                    )
                    return 1
                print(
                    f"\n[webauto-cli secrets] WARNING: a key already exists at:\n"
                    f"  {target}\n"
                    f"\n"
                    f"Overwriting will permanently orphan every encrypted env\n"
                    f"file created with the old key — they cannot be recovered.\n",
                    file=sys.stderr,
                )
                try:
                    answer = input("Type 'yes' to overwrite, anything else to abort: ")
                except (EOFError, KeyboardInterrupt):
                    print("\naborted", file=sys.stderr)
                    return 1
                if answer.strip().lower() != "yes":
                    print("aborted", file=sys.stderr)
                    return 1
                path = generate_key(args.key_file, force=True)
            else:
                path = generate_key(args.key_file, force=args.force)
            print(f"key written: {path}", file=sys.stderr)
            print("Keep this file. Anyone with it can decrypt your env files.",
                  file=sys.stderr)
            return 0

        if args.op == "encrypt":
            encrypt_file(args.input, args.out, key_path=args.key_file)
            print(f"encrypted {args.input} → {args.out}", file=sys.stderr)
            print(f"key used: {resolve_key_path(args.key_file)}", file=sys.stderr)
            return 0

        if args.op == "decrypt":
            text = decrypt_file(args.input, key_path=args.key_file)
            sys.stdout.write(text)
            if not text.endswith("\n"):
                sys.stdout.write("\n")
            return 0

        if args.op == "path":
            print(resolve_key_path(args.key_file))
            return 0

    except KeyError_ as e:
        print(f"[webauto-cli secrets] {e}", file=sys.stderr)
        return 1
    except FileNotFoundError as e:
        print(f"[webauto-cli secrets] {e}", file=sys.stderr)
        return 1

    return 2
