#!/usr/bin/env sh
# Install the ai-product-video skill for Claude Code and/or Codex.
# Usage: ./install.sh [claude|codex|all]   (default: all)
set -e
target="${1:-all}"
src="$(cd "$(dirname "$0")" && pwd)"
name="ai-product-video"

install_to() {
  dest="$1/$name"
  mkdir -p "$1"
  rm -rf "$dest"
  mkdir -p "$dest"
  # copy everything except git metadata and caches
  (cd "$src" && tar --exclude=.git --exclude=.cache --exclude=__pycache__ -cf - .) | (cd "$dest" && tar -xf -)
  echo "installed -> $dest"
}

case "$target" in
  claude) install_to "$HOME/.claude/skills" ;;
  codex)  install_to "$HOME/.agents/skills" ;;
  all)    install_to "$HOME/.claude/skills"; install_to "$HOME/.agents/skills" ;;
  *) echo "usage: $0 [claude|codex|all]" >&2; exit 2 ;;
esac
echo "Restart Claude Code / Codex if the skill does not show up."
