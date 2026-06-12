#!/usr/bin/env bash
# ============================================================
# Atlas Control — Native OSRM Build & Install (No Docker)
# Builds osrm-backend from source (Linux ARM64 or x86_64).
#
# Why native?
#   - No Docker daemon overhead (~200 MB RAM saved per state)
#   - Faster startup (~2 s vs ~15 s for Docker cold-start)
#   - Process management via Python subprocess (no Docker socket)
#   - Better suited for off-grid survival deployment
#
# Build time: ~20-40 minutes depending on CPU core count
# Disk:       ~600 MB for build deps + ~50 MB for installed binaries
#
# After install, routing_node.py auto-detects 'osrm-routed' in PATH
# and uses it instead of Docker — no configuration needed.
#
# Usage:
#   bash setup_native_osrm.sh                 # build and install
#   bash setup_native_osrm.sh --check         # check if already installed
#   bash setup_native_osrm.sh --no-cleanup-prompt
# ============================================================
set -euo pipefail

OSRM_VERSION="v5.27.1"   # last stable v5 release
BUILD_DIR="${HOME}/osrm-build"
INSTALL_PREFIX="/usr/local"
JOBS=$(nproc)
NO_CLEANUP_PROMPT=0

for arg in "$@"; do
    case "$arg" in
        --no-cleanup-prompt)
            NO_CLEANUP_PROMPT=1
            ;;
    esac
done

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

# ── Check if already installed ─────────────────────────────────────────────
if [[ "${1:-}" == "--check" ]]; then
    if command -v osrm-routed &>/dev/null; then
        VER=$(osrm-routed --version 2>/dev/null | head -1 || echo "unknown")
        echo -e "${GREEN}✓ osrm-routed is installed: ${VER}${NC}"
        echo "  $(which osrm-routed)"
        exit 0
    else
        echo -e "${YELLOW}✗ osrm-routed not found${NC}"
        echo "  Run: bash setup_native_osrm.sh"
        exit 1
    fi
fi

if command -v osrm-routed &>/dev/null; then
    echo -e "${GREEN}✓ osrm-routed already installed:${NC} $(which osrm-routed)"
    echo "  routing_node.py will use native OSRM automatically."
    exit 0
fi

echo -e "${BOLD}Atlas — Build OSRM from source (ARM64 / Jetson)${NC}"
echo -e "${YELLOW}Version: ${OSRM_VERSION}${NC}"
echo -e "${YELLOW}Jobs:    ${JOBS} parallel${NC}"
echo ""

# ── Install build dependencies ─────────────────────────────────────────────
echo -e "${CYAN}→ Installing build dependencies…${NC}"
sudo apt-get update -qq
sudo apt-get install -y \
    build-essential \
    cmake \
    pkg-config \
    libboost-all-dev \
    libtbb-dev \
    libluajit-5.1-dev \
    libbz2-dev \
    libxml2-dev \
    libzip-dev \
    libgdal-dev \
    liblua5.3-dev \
    libstxxl-dev \
    git \
    curl

echo -e "${GREEN}  ✓ Dependencies installed${NC}"

# ── Clone / update source ──────────────────────────────────────────────────
echo -e "${CYAN}→ Cloning OSRM ${OSRM_VERSION}…${NC}"
if [[ -d "${BUILD_DIR}/osrm-backend" ]]; then
    echo "  (existing source found, pulling updates)"
    cd "${BUILD_DIR}/osrm-backend"
    git fetch --tags -q
    git checkout "${OSRM_VERSION}" -q
else
    mkdir -p "${BUILD_DIR}"
    cd "${BUILD_DIR}"
    git clone --depth 1 --branch "${OSRM_VERSION}" \
        https://github.com/Project-OSRM/osrm-backend.git
    cd osrm-backend
fi

echo -e "${GREEN}  ✓ Source ready${NC}"

# ── Configure ──────────────────────────────────────────────────────────────
echo -e "${CYAN}→ Configuring build (Release, native optimized)…${NC}"
mkdir -p build && cd build

cmake .. \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX="${INSTALL_PREFIX}" \
    -DENABLE_LTO=ON \
    -DBUILD_SHARED_LIBS=OFF \
    -DOSRM_BUILD_ROUTED=ON \
    -DOSRM_BUILD_TOOLS=OFF \
    -DOSRM_BUILD_TESTS=OFF \
    -DCMAKE_CXX_FLAGS="-march=native -O3" \
    -DCMAKE_C_FLAGS="-march=native -O3" \
    2>&1 | tail -5

echo -e "${GREEN}  ✓ Configured${NC}"

# ── Build ──────────────────────────────────────────────────────────────────
echo -e "${CYAN}→ Building with ${JOBS} cores (this takes 20-40 minutes)…${NC}"
echo "  Progress: make -j${JOBS}"
make -j"${JOBS}" osrm-routed osrm-extract osrm-partition osrm-customize

echo -e "${GREEN}  ✓ Build complete${NC}"

# ── Install ────────────────────────────────────────────────────────────────
echo -e "${CYAN}→ Installing to ${INSTALL_PREFIX}/bin/…${NC}"
sudo make install

echo -e "${GREEN}  ✓ Installed${NC}"

# ── Verify ────────────────────────────────────────────────────────────────
echo ""
if command -v osrm-routed &>/dev/null; then
    VER=$(osrm-routed --version 2>/dev/null | head -1 || echo "unknown")
    echo -e "${GREEN}${BOLD}✓ OSRM successfully installed!${NC}"
    echo ""
    echo "  Binary:  $(which osrm-routed)"
    echo "  Version: ${VER}"
    echo ""
    echo "  routing_node.py will automatically use native OSRM on next restart."
    echo "  All 50 state OSRM files are already processed — no re-processing needed."
    echo ""
    echo "  Memory savings vs Docker:"
    echo "    Native:  ~150-300 MB per state"
    echo "    Docker:  ~350-600 MB per state"
    echo ""
    echo "  To stop Docker containers and switch fully to native:"
    echo "    bash start_routing.sh stop"
    echo "    sudo systemctl restart atlas-control"
else
    echo -e "${RED}✗ Installation may have failed — osrm-routed not in PATH${NC}"
    echo "  Try: sudo ldconfig && hash -r"
    exit 1
fi

# ── Optional: clean up build directory ───────────────────────────────────
echo ""
if [[ "$NO_CLEANUP_PROMPT" -eq 1 ]]; then
    echo "Skipping cleanup prompt (--no-cleanup-prompt). Build directory left at ${BUILD_DIR}."
else
    read -rp "Clean up build directory (${BUILD_DIR}) to free ~2 GB? [y/N]: " CLEANUP
    if [[ "${CLEANUP,,}" == "y" ]]; then
        rm -rf "${BUILD_DIR}"
        echo -e "${GREEN}  ✓ Build directory removed${NC}"
    fi
fi
