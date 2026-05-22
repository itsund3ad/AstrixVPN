#!/usr/bin/env bash
# Astrix — build all executables for release
# Usage: ./scripts/build-all.sh [--no-upx] [--no-win]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"
VERSION="$(cat "$ROOT/VERSION" 2>/dev/null || echo "0.0.0")"
USE_UPX=true
BUILD_WIN=true

for arg in "$@"; do
  case "$arg" in
    --no-upx) USE_UPX=false ;;
    --no-win) BUILD_WIN=false ;;
    *) echo "Unknown option: $arg"; exit 1 ;;
  esac
done

cd "$ROOT"

echo "═══ Astrix Build v$VERSION ═══"
echo ""

# ── Linux client ──
echo ">>> [1/4] Building astrix-client (Linux)..."
cd astrix-client
python build_exe.py
cd "$ROOT"
CLIENT_BIN="dist/astrix-client-v$VERSION"
if [ -f "$CLIENT_BIN" ]; then
  if $USE_UPX && command -v upx &>/dev/null; then
    echo "    UPX compressing client..."
    upx --best --lzma "$CLIENT_BIN" 2>/dev/null || true
  fi
  echo "    ✓ $CLIENT_BIN ($(du -sh "$CLIENT_BIN" | cut -f1))"
else
  echo "    ✗ Client binary not found!"
  exit 1
fi

# ── Linux server ──
echo ">>> [2/4] Building astrix-server (Linux)..."
cd astrix-server
python build_exe.py
cd "$ROOT"
SERVER_BIN="dist/astrix-server-v$VERSION"
if [ -f "$SERVER_BIN" ]; then
  if $USE_UPX && command -v upx &>/dev/null; then
    echo "    UPX compressing server..."
    upx --best --lzma "$SERVER_BIN" 2>/dev/null || true
  fi
  echo "    ✓ $SERVER_BIN ($(du -sh "$SERVER_BIN" | cut -f1))"
else
  echo "    ✗ Server binary not found!"
  exit 1
fi

# ── Linux release archives ──
echo ">>> [3/4] Creating release archives..."
cd dist
for f in astrix-client-v"$VERSION" astrix-server-v"$VERSION"; do
  if [ -f "$f" ]; then
    tar czf "$f-linux-amd64.tar.gz" "$f"
    echo "    ✓ $f-linux-amd64.tar.gz"
  fi
done
cd "$ROOT"

# ── Windows cross-build (if Docker available) ──
if $BUILD_WIN && command -v docker &>/dev/null; then
  echo ">>> [4/4] Cross-building Windows binaries (Docker)..."
  if [ -f "scripts/docker/Dockerfile.win" ]; then
    DOCKER_BUILDKIT=1 docker build \
      --build-arg VERSION="$VERSION" \
      -f scripts/docker/Dockerfile.win \
      --output dist/ . && \
    echo "    ✓ Windows binaries in dist/"
  else
    echo "    ⚠ scripts/docker/Dockerfile.win not found; skipping Windows"
  fi
else
  echo ">>> [4/4] Skipping Windows (Docker not available)"
fi

echo ""
echo "═══ Build complete ═══"
echo "Artifacts in: $ROOT/dist/"
ls -lh dist/ 2>/dev/null || true
