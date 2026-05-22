SHELL := /bin/bash
VERSION := $(shell cat VERSION 2>/dev/null || echo "0.0.0")
UPX := $(shell command -v upx 2>/dev/null)

.PHONY: all build-client-linux build-server-linux build-all-linux \
        build-client-win build-server-win build-all-win build-all \
        clean distclean release

all: build-all-linux

# ── Linux builds ───────────────────────────────────────────

build-client-linux:
	@echo "Building astrix-client v$(VERSION) (Linux)..."
	@cd astrix-client && python build_exe.py
	@echo "Done: dist/astrix-client-v$(VERSION)"
	@if [ -n "$(UPX)" ]; then \
		echo "Compressing with UPX..."; \
		upx --best --lzma dist/astrix-client-v$(VERSION) 2>/dev/null; \
	fi

build-server-linux:
	@echo "Building astrix-server v$(VERSION) (Linux)..."
	@cd astrix-server && python build_exe.py
	@echo "Done: dist/astrix-server-v$(VERSION)"
	@if [ -n "$(UPX)" ]; then \
		echo "Compressing with UPX..."; \
		upx --best --lzma dist/astrix-server-v$(VERSION) 2>/dev/null; \
	fi

build-all-linux: build-client-linux build-server-linux

# ── Windows cross builds (via Docker) ──────────────────────

build-client-win:
	@echo "Cross-building astrix-client v$(VERSION) (Windows) via Docker..."
	@DOCKER_BUILDKIT=1 docker build \
		--build-arg VERSION=$(VERSION) \
		-f scripts/docker/Dockerfile.win \
		--output dist/ .
	@echo "Done: dist/astrix-client-v$(VERSION).exe"

build-server-win:
	@echo "Cross-building astrix-server v$(VERSION) (Windows) via Docker..."
	@DOCKER_BUILDKIT=1 docker build \
		--build-arg VERSION=$(VERSION) \
		-f scripts/docker/Dockerfile.win \
		--output dist/ .
	@echo "Done: dist/astrix-server-v$(VERSION).exe"

build-all-win: build-client-win build-server-win

# ── All platforms ──────────────────────────────────────────

build-all: build-all-linux build-all-win

# ── Release artifacts ──────────────────────────────────────

release: build-all-linux
	@echo ""
	@echo "Packaging release artifacts..."
	@cd dist && for f in astrix-client-v$(VERSION) astrix-server-v$(VERSION); do \
		if [ -f "$$f" ]; then \
			tar czf "$$f-linux-amd64.tar.gz" "$$f"; \
			echo "  Created: $$f-linux-amd64.tar.gz"; \
		fi; \
	done
	@echo "Release artifacts in dist/"

# ── Clean ──────────────────────────────────────────────────

clean:
	@rm -rf \
		astrix-client/build \
		astrix-client/dist \
		astrix-client/*.spec \
		astrix-server/build \
		astrix-server/dist \
		astrix-server/*.spec

distclean: clean
	@rm -rf dist
	@echo "Cleaned all build artifacts"

# ── Help ──────────────────────────────────────────────────

help:
	@echo "Astrix Makefile v$(VERSION)"
	@echo ""
	@echo "Targets:"
	@echo "  build-client-linux   Build client binary for Linux"
	@echo "  build-server-linux   Build server binary for Linux"
	@echo "  build-all-linux      Build both Linux binaries"
	@echo "  build-client-win     Cross-build client .exe (Docker)"
	@echo "  build-server-win     Cross-build server .exe (Docker)"
	@echo "  build-all-win        Build both Windows binaries"
	@echo "  build-all            Build all platforms"
	@echo "  release              Build Linux + create .tar.gz archives"
	@echo "  clean                Remove build artifacts"
	@echo "  distclean            Remove everything (including dist/)"
	@echo "  help                 This message"
