#!/usr/bin/env bash
# Astrix — cross-build Windows .exe via Docker + wine + PyInstaller
# Does NOT require a Windows machine — runs entirely on Linux.
#
# Prerequisites: docker, make, python3
# Usage: ./scripts/cross-build-win.sh [client|server|all]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"
VERSION="$(cat "$ROOT/VERSION" 2>/dev/null || echo "0.0.0")"
TARGET="${1:-all}"

# Ensure Dockerfile.win exists
DOCKERFILE="$SCRIPT_DIR/docker/Dockerfile.win"
mkdir -p "$SCRIPT_DIR/docker"

# Generate Dockerfile for Windows cross-build
cat > "$DOCKERFILE" << 'DOCKER_EOF'
# syntax=docker/dockerfile:1
FROM --platform=linux/amd64 ubuntu:22.04 AS builder

ARG VERSION=0.0.0
ENV VERSION=${VERSION}
ENV DEBIAN_FRONTEND=noninteractive

# Install wine + python
RUN dpkg --add-architecture i386 && \
    apt-get update -qq && \
    apt-get install -y -qq \
      wine64 wine32 \
      python3-pip \
      upx-ucl \
      xvfb \
      --no-install-recommends && \
    rm -rf /var/lib/apt/lists/*

# Setup wine
ENV WINEPREFIX=/wine
ENV WINEDLLOVERRIDES="mscoree,mshtml="
RUN wineboot --init 2>/dev/null; \
    wineboot --update 2>/dev/null; \
    true

WORKDIR /build

# Copy source
COPY VERSION .
COPY astrix-client astrix-client/
COPY astrix-server astrix-server/
COPY client_config.example.json .
COPY server_config.example.json .
COPY scripts/versioninfo.txt scripts/

# Install Python deps under wine
RUN xvfb-run -a wine python -m pip install --upgrade pip setuptools wheel 2>/dev/null || true
RUN xvfb-run -a wine python -m pip install pyinstaller 2>/dev/null || true

# Build client
RUN cd astrix-client && \
    xvfb-run -a wine python build_exe.py && \
    mv dist/astrix-client-v${VERSION}.exe /build/astrix-client-v${VERSION}-windows-amd64.exe 2>/dev/null || \
    mv dist/*.exe /build/astrix-client-v${VERSION}-windows-amd64.exe 2>/dev/null || true

# Build server
RUN cd astrix-server && \
    xvfb-run -a wine python build_exe.py && \
    mv dist/astrix-server-v${VERSION}.exe /build/astrix-server-v${VERSION}-windows-amd64.exe 2>/dev/null || \
    mv dist/*.exe /build/astrix-server-v${VERSION}-windows-amd64.exe 2>/dev/null || true

# Compress with UPX
RUN if command -v upx &>/dev/null; then \
      upx --best --lzma /build/*.exe 2>/dev/null || true; \
    fi

FROM scratch
COPY --from=builder /build/*.exe /
DOCKER_EOF

echo "═══ Astrix Windows Cross-Build v$VERSION ═══"
echo "Target: $TARGET"
echo ""

# Build with Docker
DOCKER_BUILDKIT=1 docker build \
  --build-arg VERSION="$VERSION" \
  -f "$DOCKERFILE" \
  --output "$ROOT/dist/" \
  "$ROOT"

echo ""
echo "═══ Done ═══"
echo "Windows binaries in: $ROOT/dist/"
ls -lh "$ROOT/dist/"*.exe 2>/dev/null || true
