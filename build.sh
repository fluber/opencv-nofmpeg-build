#!/usr/bin/env bash
# build.sh — 編譯一份不含 FFmpeg、只含 lottery-reader / yolo-trainer 實際用到模組的
#   OpenCV 5.0.0,產出可以直接裝進這兩個專案 .venv 的 wheel。
#
# 背景:opencv-contrib-python(-headless) 的官方 wheel 一律內建 FFmpeg(LGPLv2.1)。
#   lottery-reader 的相機/影像功能全部走 Linux 的 V4L2(cv2.VideoCapture(int) 在
#   Linux 上自動選 V4L2 後端,已實測確認),完全用不到 FFmpeg;兩個專案的程式碼也
#   都沒用到任何 opencv_contrib 專屬模組(aruco/ximgproc/xfeatures2d/bgsegm/...
#   都沒有,SIFT 用的是已回到主模組的 cv2.SIFT_create,不是 contrib 的
#   cv2.xfeatures2d.SIFT_create)。所以直接編主模組、關掉 FFMPEG,兩個專案共用
#   同一份 wheel。
#
# 用法：
#   ./build.sh                 # clone（若不存在）＋ configure ＋ build ＋ 打包 wheel
#   ./build.sh --reconfigure   # 保留 opencv/ 原始碼，重跑 cmake configure（改參數後用）
#
# ⚠ 出貨用的 wheel 要在 ubuntu:22.04 容器裡編（ci/build_in_container.sh，CI 也是跑這支）。
#   直接在較新的系統上跑這支，package_wheel.py 的 glibc/libstdc++ 護欄會擋下來——
#   那份 .so 裝到部署目標的 Ubuntu 22.04 會 ImportError。本機直接跑只適合試參數。
#
# 可用環境變數覆寫（CI 容器裡就是這樣指到容器內的 Python）：
#   DEPLOY_PY / PYBIN / PYINC / PYLIB   目標 Python（預設 lottery-reader 的出貨 Python）
#   NPINC                               numpy 標頭目錄
#   JOBS                                ninja 平行數（預設 4）
#
# 輸出：dist/opencv_headless_noffmpeg-5.0.0-cp312-cp312-linux_x86_64.whl
#
# 對應的目標 Python：lottery-reader/deploy/vendor/x86_64/python（3.12.12，
#   出貨版離線 Python，跟 lottery-reader 的 .venv 版本一致）。yolo-trainer 目前
#   還沒有自己的 deploy/vendor，等它建好離線安裝流程時，比照這裡的模式建一份。

set -euo pipefail
cd "$(dirname "$0")"

OPENCV_TAG="5.0.0"
DEPLOY_PY="${DEPLOY_PY:-/home/fluber/Projects/lottery-reader/deploy/vendor/$(uname -m)/python}"
PYBIN="${PYBIN:-$DEPLOY_PY/bin/python3.12}"
PYINC="${PYINC:-$DEPLOY_PY/include/python3.12}"
PYLIB="${PYLIB:-$DEPLOY_PY/lib/libpython3.12.so}"
# numpy 版本要跟 deploy 實際會裝的一致（deploy/vendor/.../wheels/numpy-*.whl）；
# 用 lottery-reader 開發 .venv 的 numpy include，版本剛好對得上（2.5.3）。
NPINC="${NPINC:-/home/fluber/Projects/lottery-reader/.venv/lib/python3.12/site-packages/numpy/_core/include}"
JOBS="${JOBS:-4}"
for f in "$PYBIN" "$PYINC/Python.h" "$PYLIB" "$NPINC/numpy/ndarrayobject.h"; do
  [ -e "$f" ] || { echo "[build] 找不到 $f（目標 Python/numpy 路徑不對？見檔頭的環境變數）"; exit 1; }
done

# 只列實際用到的模組——core/imgproc/imgcodecs/videoio/video/calib/features/flann/
# geometry/highgui/python/python3/python_bindings_generator。calib 在 OpenCV 5.x
# 會連帶拉 objdetect、stereo（模組依賴，不是我們要的，但很小，不用管）。
# ⚠ OpenCV 5.x 模組改名了：calib3d→calib、features2d→features、python3→python
#   （bindings 產生器另外要 python_bindings_generator，不會自動被 BUILD_LIST 帶到，
#   漏了會整個 Python 綁定被 whitelist 擋掉、只留 C++ 函式庫，import cv2 會找不到）。
BUILD_LIST="core,imgproc,imgcodecs,videoio,video,calib,features,flann,geometry,highgui,python,python3,python_bindings_generator"

# BUILD_SHARED_LIBS=OFF：把 core/imgproc/... 全部靜態連進單一 cv2.so，不要各自
# 產生 libopencv_*.so。2026-09-26 第一次用 ON 建過一次——cv2.so 動態連結十幾個
# libopencv_*.so.5.0.0，官方 wheel 靠 auditwheel 把這些依賴一起打包進 wheel、
# 改寫 rpath，這台機器沒裝 auditwheel/patchelf，手動搬那些 .so、算 rpath 太脆弱。
# 改靜態連結後就是單一檔案，直接複製進 site-packages/cv2/ 就能動，跟官方
# opencv-python wheel 對第三方庫（FFmpeg 那些）的處理方式一致——那些也是靜態或
# auditwheel 打包，只是我們用不到 FFmpeg，問題不存在。

if [ ! -d opencv ]; then
  echo "[build] clone opencv @ $OPENCV_TAG ..."
  git clone --branch "$OPENCV_TAG" --depth 1 https://github.com/opencv/opencv.git opencv
fi

mkdir -p build
cd build

if [ "${1:-}" = "--reconfigure" ] || [ ! -f CMakeCache.txt ]; then
  # BUILD_JPEG/BUILD_PNG=ON 逼 OpenCV 用自己 3rdparty/ 底下內建的 libjpeg-turbo/
  # libpng 原始碼編、靜態連進 cv2.so，不要連建置機系統的 /usr/lib/.../libjpeg.so、
  # libpng.so。2026-09-26 差點漏掉：第一次只開 WITH_JPEG/WITH_PNG=ON，cmake 找到
  # 系統版就直接動態連結，但離線安裝包不會另外帶 libjpeg/libpng 這些系統庫
  # ——官方 opencv wheel 本來就是整個自帶、不依賴系統庫，這樣改才會一致，
  # 不然部署機缺這兩個系統庫會直接掛掉。
  cmake -GNinja \
    -D CMAKE_BUILD_TYPE=Release \
    -D BUILD_LIST="$BUILD_LIST" \
    -D WITH_FFMPEG=OFF \
    -D WITH_V4L=ON \
    -D WITH_GTK=OFF \
    -D WITH_QT=OFF \
    -D WITH_JPEG=ON \
    -D WITH_PNG=ON \
    -D BUILD_JPEG=ON \
    -D BUILD_PNG=ON \
    -D BUILD_ZLIB=ON \
    -D BUILD_TIFF=ON \
    -D BUILD_WEBP=ON \
    -D BUILD_OPENJPEG=ON \
    -D WITH_GSTREAMER=OFF \
    -D WITH_1394=OFF \
    -D OPENCV_ENABLE_NONFREE=OFF \
    -D BUILD_SHARED_LIBS=OFF \
    -D BUILD_TESTS=OFF \
    -D BUILD_PERF_TESTS=OFF \
    -D BUILD_EXAMPLES=OFF \
    -D BUILD_DOCS=OFF \
    -D BUILD_opencv_apps=OFF \
    -D BUILD_opencv_python3=ON \
    -D CMAKE_INSTALL_PREFIX="$PWD/stage" \
    -D OPENCV_PYTHON3_INSTALL_PATH=python \
    -D PYTHON3_EXECUTABLE="$PYBIN" \
    -D PYTHON3_INCLUDE_DIR="$PYINC" \
    -D PYTHON3_LIBRARY="$PYLIB" \
    -D PYTHON3_NUMPY_INCLUDE_DIRS="$NPINC" \
    -D Python3_EXECUTABLE="$PYBIN" \
    -D Python3_INCLUDE_DIR="$PYINC" \
    -D Python3_LIBRARY="$PYLIB" \
    -D Python3_NumPy_INCLUDE_DIR="$NPINC" \
    ../opencv
fi

echo "[build] ninja（$JOBS 核，4 核約十幾分鐘）..."
ninja -j"$JOBS"

# 裝到 build/stage/：cv2/ loader 跟 .so 會在 stage/python/cv2/，授權檔在 stage/share/licenses/。
# 先清掉舊的 stage，免得上一次建置殘留的檔案被打進 wheel。
rm -rf stage
cmake --install . > /dev/null

echo "[build] 打包 wheel ..."
"$PYBIN" ../package_wheel.py --stage "$PWD/stage" --out "$(dirname "$PWD")/dist"
