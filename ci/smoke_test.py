"""ci/smoke_test.py — 裝好 wheel 之後的冒煙測試。任何一項不過就 exit 1。

檢查：
  1. 建置資訊：FFmpeg / GStreamer 確實是 NO、V4L 是 YES（相機功能靠它）
  2. lottery-reader / yolo-trainer / lottery-calib 程式碼裡用到的每一個 cv2.xxx 都存在
     ——模組是用 BUILD_LIST 精挑的，少編一個模組不會在編譯時出錯，只會在客戶機上
     AttributeError。清單是 2026-09-28 從三個專案 grep 出來的，專案用到新的 cv2 函式要補進來。
  3. 實際跑幾個核心功能：JPEG/PNG 編解碼、SIFT、findHomography、warpPerspective
"""
import re
import sys

import cv2
import numpy as np

failed = []


def check(ok: bool, msg: str) -> None:
    print(("  ✓ " if ok else "  ✗ ") + msg)
    if not ok:
        failed.append(msg)


print(f"cv2 {cv2.__version__}  ←  {cv2.__file__}")
info = cv2.getBuildInformation()


def build_flag(name: str) -> str:
    m = re.search(rf"^\s*{name}:\s*(\S+)", info, re.MULTILINE)
    return m.group(1) if m else "(沒列出)"


print("[1] 建置資訊")
check(cv2.__version__.startswith("5.0.0"), f"版本 {cv2.__version__}")
check(build_flag("FFMPEG") in ("NO", "(沒列出)"), f"FFMPEG: {build_flag('FFMPEG')}")
check(build_flag("GStreamer") in ("NO", "(沒列出)"), f"GStreamer: {build_flag('GStreamer')}")
check(build_flag("v4l/v4l2") == "YES" or "V4L" in info and re.search(r"v4l.*YES", info, re.I) is not None,
      f"V4L: {build_flag('v4l/v4l2')}")

print("[2] 三個專案用到的 cv2 名稱")
USED = """
ADAPTIVE_THRESH_GAUSSIAN_C ADAPTIVE_THRESH_MEAN_C adaptiveThreshold addWeighted approxPolyDP
arcLength BFMatcher bitwise_not BORDER_CONSTANT boundingRect boxFilter boxPoints Canny
CAP_PROP_AUTO_EXPOSURE CAP_PROP_BRIGHTNESS CAP_PROP_BUFFERSIZE CAP_PROP_CONTRAST CAP_PROP_EXPOSURE
CAP_PROP_FOURCC CAP_PROP_FRAME_HEIGHT CAP_PROP_FRAME_WIDTH CAP_PROP_GAMMA CAP_PROP_SHARPNESS
CC_STAT_AREA CC_STAT_HEIGHT CC_STAT_WIDTH CHAIN_APPROX_NONE CHAIN_APPROX_SIMPLE circle
COLOR_BGR2GRAY COLOR_BGR2HSV COLOR_HSV2BGR connectedComponentsWithStats contourArea convexHull
countNonZero CV_32F CV_64F cvtColor destroyAllWindows dilate DIST_L2 drawMarker error
estimateAffinePartial2D EVENT_LBUTTONDOWN EVENT_LBUTTONUP EVENT_MOUSEMOVE findContours
findHomography findTransformECC fitLine flip FONT_HERSHEY_SIMPLEX GaussianBlur
getPerspectiveTransform getRotationMatrix2D getStructuringElement getWindowProperty imdecode
imencode imread IMREAD_COLOR IMREAD_GRAYSCALE imshow imwrite IMWRITE_JPEG_QUALITY inRange
INTER_AREA INTER_CUBIC INTER_LINEAR INTER_NEAREST invertAffineTransform Laplacian line LINE_AA
LMEDS magnitude MARKER_CROSS matchTemplate medianBlur minAreaRect minMaxLoc moments MORPH_CLOSE
MORPH_ELLIPSE morphologyEx MORPH_OPEN MORPH_RECT namedWindow normalize NORM_L2 NORM_MINMAX
perspectiveTransform polylines putText RANSAC rectangle resize resizeWindow RETR_EXTERNAL
RETR_LIST RETR_TREE rotate ROTATE_180 setMouseCallback SIFT_create Sobel THRESH_BINARY
THRESH_BINARY_INV threshold THRESH_OTSU TM_CCOEFF_NORMED transform VideoCapture
VideoWriter_fourcc waitKey warpAffine WARP_INVERSE_MAP warpPerspective WINDOW_AUTOSIZE
WINDOW_NORMAL WND_PROP_VISIBLE
""".split()
missing = [n for n in USED if not hasattr(cv2, n)]
check(not missing, f"{len(USED) - len(missing)}/{len(USED)} 個都在" + (f"，缺：{' '.join(missing)}" if missing else ""))

print("[3] 實際功能")
rng = np.random.default_rng(0)
img = (rng.random((240, 320, 3)) * 255).astype(np.uint8)
cv2.rectangle(img, (60, 40), (200, 180), (255, 255, 255), -1)
cv2.circle(img, (250, 120), 40, (0, 0, 0), -1)

for ext in (".jpg", ".png"):
    ok, buf = cv2.imencode(ext, img)
    dec = cv2.imdecode(buf, cv2.IMREAD_COLOR) if ok else None
    check(dec is not None and dec.shape == img.shape, f"imencode/imdecode {ext}（{len(buf) if ok else 0} bytes）")
ok, buf = cv2.imencode(".png", img)
check(ok and np.array_equal(cv2.imdecode(buf, cv2.IMREAD_COLOR), img), "PNG 無損往返")

gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
kps, desc = cv2.SIFT_create().detectAndCompute(gray, None)
check(len(kps) > 10 and desc is not None, f"SIFT 特徵點 {len(kps)} 個")

src = np.float32([[0, 0], [319, 0], [319, 239], [0, 239]])
dst = np.float32([[10, 5], [300, 15], [310, 230], [5, 220]])
H, _ = cv2.findHomography(src, dst, cv2.RANSAC)
warped = cv2.warpPerspective(img, H, (320, 240))
check(H is not None and warped.shape == img.shape, "findHomography + warpPerspective")

cap = cv2.VideoCapture()
check(not cap.isOpened(), "VideoCapture 可建立（容器裡沒有相機，只驗證類別可用）")

if failed:
    print(f"\n✗ 冒煙測試失敗 {len(failed)} 項")
    sys.exit(1)
print("\n✓ 冒煙測試全部通過")
