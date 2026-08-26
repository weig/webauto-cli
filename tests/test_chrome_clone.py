"""Chrome code-sign clone sweep — the macOS UC-launch leak fix."""
from __future__ import annotations

import os

from webauto._chrome_clone import (
    clone_root_dirs,
    sweep_chrome_code_sign_clones,
)


def _make_clone(container, bundle_id, name="code_sign_clone.abc123"):
    root = os.path.join(container, "X", f"{bundle_id}.code_sign_clone")
    os.makedirs(root, exist_ok=True)
    entry = os.path.join(root, name)
    os.makedirs(entry, exist_ok=True)
    # give the clone a file so it's a non-empty dir like the real thing
    open(os.path.join(entry, "Info.plist"), "w").close()
    return entry


def test_sweeps_orphaned_clones_across_channels(tmp_path):
    container = str(tmp_path)
    chrome = _make_clone(container, "com.google.Chrome")
    chromium = _make_clone(container, "org.chromium.Chromium")
    canary = _make_clone(container, "com.google.Chrome.canary")

    removed = sweep_chrome_code_sign_clones(container_dir=container)

    assert removed == 3
    for e in (chrome, chromium, canary):
        assert not os.path.exists(e)
    # the .code_sign_clone root dirs may remain empty — only the clones go
    assert os.path.isdir(os.path.dirname(chrome))


def test_returns_zero_when_nothing_to_sweep(tmp_path):
    os.makedirs(os.path.join(str(tmp_path), "X"), exist_ok=True)
    assert sweep_chrome_code_sign_clones(container_dir=str(tmp_path)) == 0


def test_guard_never_touches_non_clone_paths(tmp_path):
    """A directory that merely lives under X/ but isn't a code_sign_clone
    entry must be left alone."""
    container = str(tmp_path)
    # A real-looking clone (removed) next to an innocent sibling (kept).
    clone = _make_clone(container, "com.google.Chrome")
    innocent_root = os.path.join(container, "X", "com.google.Chrome.code_sign_clone")
    keep = os.path.join(innocent_root, "not_a_clone_dir")
    os.makedirs(keep, exist_ok=True)

    removed = sweep_chrome_code_sign_clones(container_dir=container)

    assert removed == 1
    assert not os.path.exists(clone)
    assert os.path.isdir(keep)          # wrong prefix → never removed


def test_clone_root_dirs_lists_only_container_X(tmp_path):
    container = str(tmp_path)
    _make_clone(container, "com.google.Chrome")
    roots = clone_root_dirs(container_dir=container)
    assert len(roots) == 1
    assert roots[0].endswith("com.google.Chrome.code_sign_clone")


def test_bad_container_is_a_safe_noop(tmp_path):
    assert sweep_chrome_code_sign_clones(container_dir=str(tmp_path / "nope")) == 0
