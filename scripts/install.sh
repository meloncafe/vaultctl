#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# vaultctl installer (source / git)
#
# Clones the repository into a stable location and installs it into a
# self-contained venv, symlinking `vaultctl` (and `vc`) onto your PATH. This is
# re-runnable and is what `vaultctl self-update` fast-forwards (dctl-style), so
# script-installed machines track the repo's latest source — not a prebuilt
# package.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/meloncafe/vaultctl/main/scripts/install.sh | bash
#
# Options (env):
#   VAULTCTL_REPO=owner/repo    # default: meloncafe/vaultctl
#   VAULTCTL_REF=main           # branch / tag / sha to install
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

REPO="${VAULTCTL_REPO:-meloncafe/vaultctl}"
REF="${VAULTCTL_REF:-main}"
SHARE_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/vaultctl"
BIN_DIR="$HOME/.local/bin"
VENV="$SHARE_DIR/.venv"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info() { echo -e "${GREEN}[vaultctl]${NC} $*"; }
warn() { echo -e "${YELLOW}[vaultctl]${NC} $*" >&2; }
err()  { echo -e "${RED}[vaultctl]${NC} $*" >&2; exit 1; }

# ── dependencies ─────────────────────────────────────────────────────────────
command -v git >/dev/null 2>&1 || err "git is required. Install git and retry."

# vaultctl targets Python 3.14; prefer an exact python3.14, else fall back.
PYTHON=""
for cand in python3.14 python3; do
  if command -v "$cand" >/dev/null 2>&1; then PYTHON="$cand"; break; fi
done
[ -n "$PYTHON" ] || err "python3 (3.14) is required."
pyver="$("$PYTHON" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo "")"
if [ "$pyver" != "3.14" ]; then
  warn "vaultctl targets Python 3.14 (found ${pyver:-unknown}); install may fail. Install python3.14 if so."
fi
"$PYTHON" -m venv --help >/dev/null 2>&1 || \
  err "python venv module missing. On Debian/Ubuntu: sudo apt install python3-venv"

# ── clone or fast-forward the checkout ───────────────────────────────────────
mkdir -p "$(dirname "$SHARE_DIR")"
if [ -d "$SHARE_DIR/.git" ]; then
  info "Updating source in $SHARE_DIR ..."
  git -C "$SHARE_DIR" fetch --quiet origin "$REF" || warn "fetch failed; using the existing checkout"
  git -C "$SHARE_DIR" checkout --quiet "$REF" 2>/dev/null || true
  git -C "$SHARE_DIR" merge --ff-only "origin/$REF" --quiet 2>/dev/null || \
    warn "could not fast-forward (local changes?); installing the current checkout"
else
  info "Cloning $REPO ($REF) into $SHARE_DIR ..."
  git clone --quiet "https://github.com/$REPO.git" "$SHARE_DIR"
  git -C "$SHARE_DIR" checkout --quiet "$REF" 2>/dev/null || true
fi

# ── venv + editable install ──────────────────────────────────────────────────
if [ ! -d "$VENV" ]; then
  info "Creating venv ..."
  "$PYTHON" -m venv "$VENV"
fi
info "Installing (this can take a moment) ..."
"$VENV/bin/pip" install --quiet --upgrade pip || warn "pip self-upgrade failed; continuing"
"$VENV/bin/pip" install --quiet -e "$SHARE_DIR" || err "install failed (see pip output above)"

# ── symlink onto PATH ────────────────────────────────────────────────────────
mkdir -p "$BIN_DIR"
ln -sf "$VENV/bin/vaultctl" "$BIN_DIR/vaultctl"
ln -sf "$VENV/bin/vc" "$BIN_DIR/vc" 2>/dev/null || true

# ── ensure ~/.local/bin is on PATH ───────────────────────────────────────────
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *)
    added=""
    for rc in "$HOME/.bashrc" "$HOME/.zshrc"; do
      [ -f "$rc" ] || continue
      if ! grep -q 'vaultctl: add ~/.local/bin to PATH' "$rc" 2>/dev/null; then
        {
          echo ''
          echo '# vaultctl: add ~/.local/bin to PATH'
          echo 'export PATH="$HOME/.local/bin:$PATH"'
        } >> "$rc"
        added="$rc"
      fi
    done
    [ -n "$added" ] && warn "Added ~/.local/bin to PATH in $added — open a new shell (or 'source' it)."
    ;;
esac

ver="$("$BIN_DIR/vaultctl" --version 2>/dev/null || echo 'vaultctl')"
info "Installed: $ver"
info "Update later with: vaultctl self-update"
info "Next: vaultctl --help   (admins: vaultctl admin setup vault)"
