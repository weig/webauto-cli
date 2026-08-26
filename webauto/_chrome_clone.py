"""Sweep the macOS Chrome code-sign clones that UC Mode launches leave behind.

Chrome (not SeleniumBase, not the OS) makes these: CodeSignCloneManager
APFS-clones the entire Google Chrome.app bundle into

    <container>/X/com.google.Chrome.code_sign_clone/code_sign_clone.XXXXXX/

on every browser launch, so the running browser keeps a stable copy of its own
signed bundle while the real one is updated underneath it. Chrome spawns a
`code-sign-clone-cleanup` helper to remove the clone again — but that helper
dies with the process group. webauto is driven as a subprocess whose caller
(e.g. the schwab_cli daemon) terminates the process group on every exit path,
so the helper is killed before it runs and every session orphans its clones.

The cost is directory-entry count, NOT bytes: the clones are APFS copy-on-write
and share blocks with the real bundle, so `du` reports tens of GB while `df`
never moves. Never gate this sweep on disk usage. Left unbounded, the entry
count eventually stalls boot (dirhelper's boot-time cleanup blocks launchd).

Sweeping at launch (before starting a new driver) heals whatever prior runs
stranded, regardless of how they died; sweeping again on close clears the
current run promptly. Deleting clones out from under a live Chrome is safe —
Chrome execs from /Applications, not from the clone.

Ported from the ep-sc terminal fix (same author, same mechanism).
"""
import glob
import os
import shutil
import sys
import tempfile

# <confstr.h> _CS_DARWIN_USER_TEMP_DIR. Passed as an int on purpose: the NAME
# is a Darwin extension and is absent from os.confstr_names, so
# os.confstr("CS_DARWIN_USER_TEMP_DIR") raises ValueError.
_CS_DARWIN_USER_TEMP_DIR = 65537

# Chrome names the directory after the bundle id, so Canary/Chromium/Brave each
# get their own sibling. Match them all rather than hardcoding one channel.
_CLONE_ROOT_GLOB = "*.code_sign_clone"

# Individual clones are mkstemp-style: "code_sign_clone." + 6 random chars.
_CLONE_ENTRY_PREFIX = "code_sign_clone."


def _default_container_dir():
    """The per-user /var/folders container that holds T/, C/ and X/.

    `X` has no confstr of its own, so derive the container from the temp dir:
    /var/folders/<a>/<b>/T/ -> /var/folders/<a>/<b>

    confstr is the authority (it ignores a TMPDIR override), but fall back to
    the usual temp-dir lookups so a future Python/Darwin change degrades into a
    working sweep rather than a silent no-op.
    """
    probes = (
        lambda: os.confstr(_CS_DARWIN_USER_TEMP_DIR),
        tempfile.gettempdir,
        lambda: os.environ.get("TMPDIR", ""),
    )
    for probe in probes:
        try:
            temp_dir = probe()
        except (AttributeError, ValueError, OSError):
            continue
        # Only a real per-user container has an X/ sibling; anything else
        # (a /tmp override, an empty value) would make the glob meaningless.
        if temp_dir and "/var/folders/" in temp_dir:
            return os.path.dirname(temp_dir.rstrip(os.sep))
    return ""


def clone_root_dirs(container_dir=None):
    """Every `<bundle-id>.code_sign_clone` directory in the container's X/."""
    container = container_dir if container_dir is not None else _default_container_dir()
    if not container or not os.path.isdir(container):
        return []
    pattern = os.path.join(container, "X", _CLONE_ROOT_GLOB)
    return sorted(path for path in glob.glob(pattern) if os.path.isdir(path))


def _is_clone_entry(path):
    """Guard: only ever remove `<X>/<something>.code_sign_clone/code_sign_clone.*`.

    Keeps a surprising container value (empty, wrong, symlinked) from turning
    this into an rmtree of something that matters.
    """
    name = os.path.basename(path)
    parent = os.path.basename(os.path.dirname(path))
    grandparent = os.path.basename(os.path.dirname(os.path.dirname(path)))
    return (
        name.startswith(_CLONE_ENTRY_PREFIX)
        and len(name) > len(_CLONE_ENTRY_PREFIX)
        and parent.endswith(".code_sign_clone")
        and grandparent == "X"
        and os.path.isdir(path)
        and not os.path.islink(path)
    )


def _remove_clone(path):
    # Chrome can land a NEW clone in the tree while rmtree is walking it, which
    # surfaces as "Directory not empty". One retry clears that race.
    for _ in range(2):
        shutil.rmtree(path, ignore_errors=True)
        if not os.path.exists(path):
            return True
    return False


def sweep_chrome_code_sign_clones(container_dir=None):
    """Remove orphaned Chrome code-sign clones. Returns how many were removed.

    Best-effort and never raises: this is hygiene on a launch/cleanup path and
    must not be able to fail an automation run. A no-op off macOS, where the
    directory does not exist.
    """
    if container_dir is None and sys.platform != "darwin":
        return 0
    removed = 0
    try:
        for root in clone_root_dirs(container_dir):
            for entry in sorted(glob.glob(os.path.join(root, _CLONE_ENTRY_PREFIX + "*"))):
                if _is_clone_entry(entry) and _remove_clone(entry):
                    removed += 1
    except Exception:
        pass
    return removed
