#!/usr/bin/env bash
# Installer for the `webauto-cli` CLI (webauto project).
#
# Quick install (latest main):
#   curl -LsSf https://raw.githubusercontent.com/<owner>/<repo>/main/bin/install.sh | sh
#
# Pinned to a tag (e.g. a release):
#   WEBAUTO_REF=v0.2.0    curl -LsSf https://.../install.sh | sh
#   curl -LsSf https://.../install.sh | sh -s -- --ref v0.2.0
#
# Pinned to a branch:
#   WEBAUTO_REF=my-branch curl -LsSf https://.../install.sh | sh
#
# Pinned to a commit:
#   WEBAUTO_REF=abc1234   curl -LsSf https://.../install.sh | sh
#
# From a release wheel asset:
#   WEBAUTO_WHEEL=https://github.com/<owner>/<repo>/releases/download/v0.2.0/webauto-0.2.0-py3-none-any.whl \
#     curl -LsSf https://.../install.sh | sh
#
# From a local checkout (for dev):
#   WEBAUTO_REPO=/abs/path/to/checkout curl -LsSf https://.../install.sh | sh
#
# What it does:
#   1. Installs uv if it isn't already on PATH (via astral's official installer).
#   2. Installs the webauto package as a uv-managed tool, exposing
#      `webauto-cli` on PATH.
#   3. Runs `webauto-cli doctor` to surface any missing prerequisites.
#
# It does NOT install Chrome — `webauto-cli bootstrap --yes` handles that
# once the CLI is on PATH.

set -euo pipefail

# ---------------------------------------------------------------------------
# Config — env vars override defaults; --flags override env vars.
# ---------------------------------------------------------------------------

REPO="${WEBAUTO_REPO:-https://github.com/weig/webauto.git}"
REF="${WEBAUTO_REF:-main}"
WHEEL="${WEBAUTO_WHEEL:-}"
EDITABLE=0

# ---------------------------------------------------------------------------
# Flag parsing (for direct invocation or `sh -s --`)
# ---------------------------------------------------------------------------

usage() {
    cat <<'EOF'
Usage: install.sh [OPTIONS]

Source selection (mutually exclusive; later flag wins):
  --ref REF            Git ref (branch / tag / commit SHA). Default: main.
  --branch BRANCH      Alias for --ref BRANCH.
  --tag TAG            Alias for --ref TAG.
  --wheel URL          Install from a wheel asset URL (e.g. release artifact).
  --local PATH         Install from a local checkout directory.

Other:
  --editable           Install the source as editable (requires --local).
  -h, --help           Show this help.

Env vars (lower precedence than flags):
  WEBAUTO_REPO   Git URL or local path. Default: GitHub upstream.
  WEBAUTO_REF    Default: main.
  WEBAUTO_WHEEL  Wheel URL (overrides REPO/REF when set).
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --ref|--branch|--tag)
            [[ $# -lt 2 ]] && { echo "$1 requires a value" >&2; exit 2; }
            REF="$2"; WHEEL=""; shift 2
            ;;
        --wheel)
            [[ $# -lt 2 ]] && { echo "$1 requires a URL" >&2; exit 2; }
            WHEEL="$2"; shift 2
            ;;
        --local)
            [[ $# -lt 2 ]] && { echo "$1 requires a path" >&2; exit 2; }
            REPO="$2"; WHEEL=""; shift 2
            ;;
        --editable)
            EDITABLE=1; shift
            ;;
        -h|--help)
            usage; exit 0
            ;;
        *)
            echo "unknown arg: $1" >&2; usage; exit 2
            ;;
    esac
done

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

log()  { printf '\033[1;34m==>\033[0m %s\n' "$*" >&2; }
warn() { printf '\033[1;33m==>\033[0m %s\n' "$*" >&2; }
err()  { printf '\033[1;31m==>\033[0m %s\n' "$*" >&2; }

have() { command -v "$1" >/dev/null 2>&1; }

ensure_path() {
    if [[ ":$PATH:" != *":$HOME/.local/bin:"* ]]; then
        export PATH="$HOME/.local/bin:$PATH"
    fi
}

# ---------------------------------------------------------------------------
# Step 1: uv
# ---------------------------------------------------------------------------

ensure_uv() {
    ensure_path
    if have uv; then
        log "uv already installed ($(uv --version))"
        return
    fi
    log "installing uv via astral's official installer"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    ensure_path
    if ! have uv; then
        err "uv installation failed — see above output"
        exit 1
    fi
    log "uv installed: $(uv --version)"
}

# ---------------------------------------------------------------------------
# Step 2: install
# ---------------------------------------------------------------------------

install_tool() {
    local source_spec
    if [[ -n "$WHEEL" ]]; then
        source_spec="$WHEEL"
        log "installing webauto from wheel: ${WHEEL}"
    elif [[ -d "$REPO" ]]; then
        source_spec="$REPO"
        log "installing webauto from local checkout: ${REPO}"
    else
        source_spec="git+${REPO}@${REF}"
        log "installing webauto from ${REPO}@${REF}"
    fi

    if [[ "$EDITABLE" -eq 1 ]]; then
        if [[ ! -d "$REPO" ]]; then
            err "--editable requires --local (a local checkout path)"
            exit 2
        fi
        uv tool install --force --editable "$source_spec"
    else
        uv tool install --force "$source_spec"
    fi
}

# ---------------------------------------------------------------------------
# Step 3: verify
# ---------------------------------------------------------------------------

verify() {
    ensure_path
    if ! have webauto-cli; then
        err "webauto-cli not on PATH after install"
        warn "Either ~/.local/bin is missing from your PATH, or uv tool dir"
        warn "is elsewhere. Try: 'uv tool update-shell' and re-source your shell rc."
        exit 1
    fi
    log "webauto-cli installed: $(command -v webauto-cli)"

    # Make sure the encryption key exists. --if-missing is a silent no-op
    # when re-running the installer (upgrade), so we don't pop a confirmation
    # prompt in the middle of a curl-piped script.
    log "ensuring encryption key exists"
    webauto-cli secrets keygen --if-missing

    log "running doctor checks"
    webauto-cli doctor || {
        warn "doctor flagged issues. If Chrome is missing, run:"
        warn "    webauto-cli bootstrap --yes"
    }
}

# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

main() {
    ensure_uv
    install_tool
    verify
    echo
    log "done. Try: webauto-cli --help"
}

main
