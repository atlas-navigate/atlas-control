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
# Parallelism is capped by RAM, not just core count. OSRM's Boost.Spirit
# translation units (parameters_parser.cpp …) can use ~2.5 GB each at -O3, so a
# full -j$(nproc) OOM-kills the compiler on RAM-limited boards — on the Jetson
# Orin Nano (8 GB) this shows up as "Killed signal terminated program cc1plus"
# around 70-90%. Allow ~1 compile job per 3 GB of RAM (min 1).
JOBS=$(nproc)
MEM_GB=$(awk '/MemTotal/{printf "%d", $2/1024/1024}' /proc/meminfo)
MEM_CAP=$(( MEM_GB / 3 )); (( MEM_CAP < 1 )) && MEM_CAP=1
(( JOBS > MEM_CAP )) && JOBS=$MEM_CAP
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

# ── Temporary build swap (low-RAM boards) ───────────────────────────────────
# Jetson Orin Nano ships with 8 GB RAM and frequently 0 swap. Even with a
# capped job count the LTO link and the heaviest Boost.Spirit units spike past
# available RAM and the OOM-killer aborts the build. Add a temporary swapfile
# for the duration of the build and remove it on exit (success or failure).
ATLAS_SWAPFILE=""
cleanup_build_swap() {
    if [[ -n "$ATLAS_SWAPFILE" && -e "$ATLAS_SWAPFILE" ]]; then
        sudo swapoff "$ATLAS_SWAPFILE" 2>/dev/null || true
        sudo rm -f "$ATLAS_SWAPFILE"
    fi
}
trap cleanup_build_swap EXIT
ensure_build_swap() {
    local have_mb want_mb=8192 dir="$1"
    have_mb=$(awk '/SwapTotal/{print int($2/1024)}' /proc/meminfo)
    (( have_mb >= 4096 )) && return 0   # enough swap already
    ATLAS_SWAPFILE="${dir}/.osrm-build-swap"
    echo -e "${CYAN}→ Only ${have_mb} MB swap detected; adding a temporary ${want_mb} MB build swapfile…${NC}"
    sudo rm -f "$ATLAS_SWAPFILE"
    # fallocate can produce a holey file some kernels reject for swap; fall back to dd.
    if ! sudo fallocate -l "${want_mb}M" "$ATLAS_SWAPFILE" 2>/dev/null; then
        sudo dd if=/dev/zero of="$ATLAS_SWAPFILE" bs=1M count="$want_mb" status=none
    fi
    sudo chmod 600 "$ATLAS_SWAPFILE"
    if sudo mkswap "$ATLAS_SWAPFILE" >/dev/null 2>&1 && sudo swapon "$ATLAS_SWAPFILE" 2>/dev/null; then
        echo -e "${GREEN}  ✓ Temporary swap active (removed automatically after the build)${NC}"
    else
        echo -e "${YELLOW}  ! Could not enable swapfile — continuing without it (build may OOM)${NC}"
        sudo rm -f "$ATLAS_SWAPFILE"; ATLAS_SWAPFILE=""
    fi
}

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

# Only skip the build when the WHOLE toolset is present. osrm-routed alone is
# not enough: processing a state needs osrm-extract / osrm-partition /
# osrm-customize, and install.sh falls back here precisely when those are
# missing even though osrm-routed happens to be on PATH.
OSRM_REQUIRED_TOOLS=(osrm-routed osrm-extract osrm-partition osrm-customize)
_osrm_all_present() {
    local t
    for t in "${OSRM_REQUIRED_TOOLS[@]}"; do command -v "$t" &>/dev/null || return 1; done
}
if _osrm_all_present; then
    echo -e "${GREEN}✓ OSRM already installed:${NC} $(which osrm-routed)"
    echo "  routing_node.py will use native OSRM automatically."
    exit 0
fi

echo -e "${BOLD}Atlas — Build OSRM from source (ARM64 / Jetson)${NC}"
echo -e "${YELLOW}Version: ${OSRM_VERSION}${NC}"
echo -e "${YELLOW}Jobs:    ${JOBS} parallel${NC}"
echo ""

# ── Install build dependencies ─────────────────────────────────────────────
# DPkg::Lock::Timeout=600 makes apt WAIT (up to 10 min) when a background
# updater (unattended-upgrades / packagekit / aptd) holds the apt/dpkg lock,
# instead of dying with "Could not get lock /var/lib/apt/lists/lock".
echo -e "${CYAN}→ Installing build dependencies…${NC}"
sudo apt-get -o DPkg::Lock::Timeout=600 update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=600 install -y \
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

# ── Toolchain compatibility patch (GCC 13 / Ubuntu 24.04 / JetPack 7) ────────
# OSRM v5.27.1 predates GCC 13. cmake/warnings.cmake calls `add_warning(error)`
# (=> -Werror). Combined with the unused-variable/function warnings GCC emits
# in OSRM's own code, that turns the build fatal. Disable -Werror so warnings
# stay warnings on newer toolchains. Idempotent (line is commented after pass 1).
echo -e "${CYAN}→ Patching out -Werror for GCC/newer-toolchain compatibility…${NC}"
WERR_PATCHED=0
while IFS= read -r f; do
    sed -i -E 's/^([[:space:]]*)add_warning\(error\)/\1# add_warning(error)  # disabled by Atlas: GCC13\/24.04 compat/' "$f"; WERR_PATCHED=1
done < <(grep -rlE '^[[:space:]]*add_warning\(error\)' --include='*.cmake' --include=CMakeLists.txt . 2>/dev/null || true)
while IFS= read -r f; do sed -i 's/-Werror//g' "$f"; WERR_PATCHED=1; done < <(grep -rlF -- '-Werror' --include='*.cmake' --include=CMakeLists.txt . 2>/dev/null || true)
[[ "$WERR_PATCHED" -eq 1 ]] && echo -e "${GREEN}  ✓ -Werror disabled${NC}" || echo -e "${YELLOW}  (no -Werror directive found)${NC}"

# ── Configure ──────────────────────────────────────────────────────────────
echo -e "${CYAN}→ Configuring build (Release, native optimized)…${NC}"
mkdir -p build && cd build

# ── GCC 13 / libstdc++ 13 missing-transitive-include shim ────────────────────
# OSRM v5.27.1 predates GCC 13, which stopped pulling many standard-library
# headers in transitively. Dozens of OSRM headers use std::vector / std::transform
# / std::unique_ptr / … without #include-ing the header that defines them (e.g.
# suffix_table.hpp uses std::vector with no <vector>, which aborts the build with
# "'vector' in namespace 'std' does not name a template type"). Rather than patch
# every file, force-include the standard headers OSRM uses bare into every C++
# translation unit so the whole missing-include class is fixed at once. Each
# header below was confirmed needed against the v5.27.1 tree; force-including is
# harmless where a TU already includes it. C sources use CMAKE_C_FLAGS (below)
# and are intentionally left untouched — C++ headers must not enter C TUs.
STD_FORCE_INCLUDES=""
for _h in cstdint cstddef cstring cmath \
          string vector array map set unordered_map unordered_set \
          memory utility tuple initializer_list \
          algorithm numeric iterator functional \
          limits type_traits ostream sstream stdexcept; do
    STD_FORCE_INCLUDES+=" -include ${_h}"
done

cmake .. \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX="${INSTALL_PREFIX}" \
    -DENABLE_LTO=ON \
    -DBUILD_SHARED_LIBS=OFF \
    -DOSRM_BUILD_ROUTED=ON \
    -DOSRM_BUILD_TOOLS=OFF \
    -DOSRM_BUILD_TESTS=OFF \
    -DCMAKE_CXX_FLAGS="-march=native -O3${STD_FORCE_INCLUDES}" \
    -DCMAKE_C_FLAGS="-march=native -O3" \
    2>&1 | tail -5

echo -e "${GREEN}  ✓ Configured${NC}"

# ── Build ──────────────────────────────────────────────────────────────────
# Add temporary swap on low-RAM boards so the OOM-killer doesn't abort the LTO
# link / heaviest Boost.Spirit units. Prefer the roomy NVMe volume for it.
if [[ -d /atlas_data ]]; then ensure_build_swap /atlas_data; else ensure_build_swap "${BUILD_DIR}"; fi
echo -e "${CYAN}→ Building with ${JOBS} parallel job(s) (this takes 20-40 minutes)…${NC}"
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
