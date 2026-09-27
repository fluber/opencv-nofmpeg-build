#!/usr/bin/env bash
# ci/build_in_container.sh — 在 ubuntu:22.04 容器裡編出「出貨用」的 wheel，並做冒煙測試。
#
# 為什麼一定要在 22.04 容器裡：客戶機台最舊是 Ubuntu 22.04（glibc 2.35、GLIBCXX_3.4.30）。
# 在比較新的系統上編，cv2.so 會用到 22.04 沒有的符號版本——編譯機上跑得好好的，
# 裝到客戶機才 ImportError。lottery-reader 的保護模組也是同一個理由在 22.04 容器裡編
# （見 lottery-reader/.github/workflows/build-deb-amd64.yml）。
#
# 用法（在 repo 根目錄）：
#   docker run --rm -v "$PWD":/work -w /work ubuntu:22.04 bash ci/build_in_container.sh
#   JOBS=16 也可以用 -e JOBS=16 傳進去（預設 nproc）
# CI（.github/workflows/build-wheel.yml）跑的就是這一行。
#
# 產出：dist/opencv_headless_noffmpeg-5.0.0-cp312-cp312-linux_<arch>.whl
#   容器是 root 跑的，產物擁有者要自己改回來（CI 那邊有一步專門做）。
set -euo pipefail
cd "$(dirname "$0")/.."

# 版本全部鎖死：建置工具也是交付物的一部分，同一個 tag 不同天重建要得到同一份東西
# （lottery-reader 鎖 nuitka 版本是同一個道理）。
PYVER="3.12.12"      # = lottery-reader deploy/fetch_deps.sh 的 PYVER，出貨用 Python
NUMPY_VER="2.5.3"    # = lottery-reader 出貨 wheels 裡的 numpy
UV_VER="0.12.15"
CMAKE_VER="4.4.3"
NINJA_VER="1.13.2"

export DEBIAN_FRONTEND=noninteractive
echo "[ci] 安裝系統工具 ..."
apt-get update -qq
# 刻意不裝任何 lib*-dev（libjpeg/libpng/ffmpeg/gstreamer…）：容器裡找不到系統庫，
# OpenCV 就只能用自己 3rdparty/ 的原始碼、靜態連進 cv2.so——跟 build.sh 的 BUILD_xxx=ON 雙重保險。
apt-get install -y -qq --no-install-recommends \
  build-essential binutils ca-certificates curl git > /dev/null
ldd --version | head -1

echo "[ci] 安裝 uv $UV_VER ..."
curl -LsSf "https://astral.sh/uv/$UV_VER/install.sh" | env UV_INSTALL_DIR=/usr/local/bin sh > /dev/null
export UV_PYTHON_PREFERENCE=only-managed UV_PYTHON_INSTALL_DIR=/opt/uv-python

# 跟 fetch_deps.sh 一樣取 uv 管理的 standalone CPython——出貨包裡的就是這一份
uv python install "$PYVER"
PYROOT="$(ls -d /opt/uv-python/cpython-${PYVER}-*linux* | head -1)"
export PYBIN="$PYROOT/bin/python3.12"
export PYINC="$PYROOT/include/python3.12"
export PYLIB="$PYROOT/lib/libpython3.12.so"

# 建置工具（cmake/ninja 用 PyPI 版：22.04 apt 的 cmake 3.22 太舊不好說、版本也鎖不住）
uv venv -q -p "$PYBIN" /opt/tools
uv pip install -q -p /opt/tools/bin/python "numpy==$NUMPY_VER" "cmake==$CMAKE_VER" "ninja==$NINJA_VER"
export PATH="/opt/tools/bin:$PATH"
export NPINC="$(/opt/tools/bin/python -c 'import numpy; print(numpy.get_include())')"
export JOBS="${JOBS:-$(nproc)}"
cmake --version | head -1

# 容器裡永遠從乾淨的 build/ 開始：掛進來的 build/ 可能是別的機器/參數留下的 CMakeCache
rm -rf build dist
./build.sh

echo "[ci] 冒煙測試：在全新 venv 裡裝 wheel ..."
WHL="$(ls dist/*.whl)"
uv venv -q -p "$PYBIN" /tmp/smoke
uv pip install -q -p /tmp/smoke/bin/python "numpy==$NUMPY_VER" "$WHL"
# 在 /tmp 跑，確保 import 的是裝好的 wheel，而不是 repo 裡的東西
(cd /tmp && /tmp/smoke/bin/python /work/ci/smoke_test.py)

sha256sum "$WHL"
echo "[ci] 完成：$WHL"
