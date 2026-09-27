# opencv-nofmpeg-build

編一份**不含 FFmpeg** 的 OpenCV 5.0.0 Python wheel（x86_64，Ubuntu 22.04 以上），
給 `lottery-reader`、`yolo-trainer` 的離線安裝包使用。

## 為什麼

官方 `opencv-python`／`opencv-contrib-python`（含 headless 版）一律內建 FFmpeg，
授權是 LGPLv2.1。我們的程式用不到 FFmpeg：

- 相機功能（拍照、即時預覽）全部走 `cv2.VideoCapture(int_index)`，在 Linux 上會自動選
  **V4L2** 後端（已用 `cap.getBackendName()` 實測確認），不經過 FFmpeg。
  程式裡也沒有讀影片檔、讀 RTSP/HTTP 串流或寫出影片檔。
- 沒有用到任何 `opencv_contrib` 專屬模組（aruco/ximgproc/xfeatures2d/bgsegm/...）。
  `cv2.SIFT_create()` 是專利過期後已回到主模組的版本。

既然用不到，就直接編 OpenCV 主模組、把 FFmpeg（以及同樣是 LGPL 的 GStreamer、
libdc1394）關掉，離線安裝包裡就只剩真正用到的東西。

## 編了哪些模組

`core imgproc imgcodecs videoio video calib features flann geometry highgui python`

⚠ OpenCV 5.x 模組改名了（`calib3d`→`calib`、`features2d`→`features`）。`calib` 在
5.x 會連帶拉 `objdetect`、`stereo` 當依賴，體積很小，不影響什麼。

主要的 cmake 參數（完整見 `build.sh`）：
- `WITH_FFMPEG=OFF`、`WITH_GSTREAMER=OFF`、`WITH_1394=OFF`
- `WITH_V4L=ON` ——相機功能真正靠的後端
- `WITH_GTK=OFF`、`WITH_QT=OFF` ——部署機是 headless，不需要 GUI
- `BUILD_JPEG/PNG/ZLIB/TIFF/WEBP/OPENJPEG=ON` ——影像格式函式庫用 OpenCV 內建的原始碼
  靜態連進 `cv2.so`，不依賴部署機的系統庫（跟官方 wheel 一樣自帶）
- `BUILD_SHARED_LIBS=OFF` ——整個 OpenCV 靜態連成單一 `cv2.so`
- `OPENCV_ENABLE_NONFREE=OFF`

## 產出

`dist/opencv_headless_noffmpeg-5.0.0-cp312-cp312-linux_x86_64.whl`

- 套件名 `opencv-headless-noffmpeg`，import 名稱一樣是 `cv2`。
  **不要跟官方 opencv-python 系列裝在同一個環境**——都會寫進同一個 `cv2/` 目錄互相覆蓋。
- 綁 Python 3.12（cp312），對應部署用的 standalone CPython 3.12.12。
- wheel 的 `*.dist-info/licenses/` 帶有 OpenCV（Apache-2.0）與內建 3rdparty 元件的授權檔。

`package_wheel.py` 打包前會檢查，任何一項不過就不產出 wheel：
1. `cv2.so` 只動態連結 glibc/libstdc++/libgcc（每台 Ubuntu 都有的系統庫）
2. 需要的符號版本不超過 GLIBC_2.35、GLIBCXX_3.4.30（= Ubuntu 22.04）
3. 授權檔齊全

## 用法

### 正式版：GitHub Actions（建議）

推一個 `v*` tag，workflow（`.github/workflows/build-wheel.yml`）會在 ubuntu:22.04 容器裡
編譯、跑冒煙測試，把 wheel 掛到同名 Release：

```bash
git tag v5.0.0-1 && git push origin v5.0.0-1
```

下游專案（lottery-reader 的 `deploy/fetch_deps.sh`）從這個 Release 下載 wheel。

### 本機重現 CI 建置

跟 CI 完全同一支腳本，一樣在 ubuntu:22.04 容器裡：

```bash
docker run --rm -e JOBS=16 -v "$PWD":/work -w /work ubuntu:22.04 bash ci/build_in_container.sh
# 容器是 root 跑的，產物擁有者改回自己：
docker run --rm -v "$PWD":/work ubuntu:22.04 chown -R "$(id -u):$(id -g)" /work/build /work/dist
```

### 直接在本機跑 build.sh（只適合試參數）

```bash
./build.sh                 # clone + configure + build + 打包 wheel
./build.sh --reconfigure   # 改了 cmake 參數後重跑 configure
```

需要 `cmake`、`ninja`、gcc，以及目標 Python/numpy 的路徑（見 `build.sh` 檔頭的環境變數）。
在比 22.04 新的系統上跑，`package_wheel.py` 的 glibc 護欄會擋下打包——這是刻意的，
那份 `.so` 裝到 22.04 會 ImportError。
