#!/usr/bin/env python3
"""package_wheel.py — 把 build.sh 編好、`cmake --install` 到 build/stage/ 的 cv2 打包成 wheel。

用法（build.sh 最後會自動呼叫，用的是「目標 Python」本身，這樣 wheel 標籤才對得上）：
    <目標 python3.12> package_wheel.py [--stage build/stage] [--out dist]

輸出：dist/opencv_headless_noffmpeg-5.0.0-cp312-cp312-linux_<arch>.whl

只用標準函式庫（zipfile/hashlib），不依賴 setuptools/wheel/auditwheel——這台機器跟
CI 容器都不必多裝東西。

wheel 內容：
  cv2/                     ← OpenCV 自己的 loader（__init__.py、config*.py）＋
                             python-3.12/cv2.cpython-312-*.so（靜態連結，單一檔案）
  *.dist-info/licenses/    ← OpenCV 的 Apache-2.0 LICENSE 與內建 3rdparty
                             （libjpeg-turbo、libpng、zlib、libwebp、Intel IPP…）各自的授權檔。這份 wheel 會
                             跟著 lottery-reader 的 .deb 一起散布，授權聲明必須跟著走
                             （見 EXTRA_LICENSES：OpenCV 的 install 沒有全部裝到）。

打包前的護欄（任何一項不過就失敗，不產出 wheel）：
  1. NEEDED 白名單：cv2.so 只能動態連結 glibc/libstdc++/libgcc 這些「每台 Ubuntu 都有」
     的系統庫。連到 libavcodec/libgstreamer 之類（授權問題）或 libjpeg/libpng 之類
     （客戶機台不保證有、.deb 也沒宣告相依）都擋下。
  2. glibc / libstdc++ 符號版本上限：預設 GLIBC_2.35、GLIBCXX_3.4.30（= Ubuntu 22.04，
     最舊的交付對象）。在比較新的系統上編（例如 26.04 開發機）會在這裡被擋——那份 .so
     在編譯機上跑得好好的，裝到客戶機才 ImportError，是最安靜的那種壞法。
     可用環境變數 MAX_GLIBC / MAX_GLIBCXX 覆寫（例如本機試編時設很大，只是別拿去出貨）。
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

DIST_NAME = "opencv_headless_noffmpeg"   # wheel 檔名用底線；METADATA 的 Name 用連字號
PROJECT_NAME = "opencv-headless-noffmpeg"
VERSION = "5.0.0"

MAX_GLIBC = os.environ.get("MAX_GLIBC", "2.35")
MAX_GLIBCXX = os.environ.get("MAX_GLIBCXX", "3.4.30")

# 客戶機台一定有的系統庫（Ubuntu 的 libc6/libstdc++6/libgcc-s1 都是 apt 本身的相依，拿不掉）
ALLOWED_NEEDED = re.compile(
    r"^(libc\.so\.6|libm\.so\.6|libstdc\+\+\.so\.6|libgcc_s\.so\.1|libpthread\.so\.0"
    r"|libdl\.so\.2|librt\.so\.1|ld-linux[-\w.]*\.so\.\d+)$"
)
# 白名單已經會擋，這份只是讓錯誤訊息講清楚「為什麼這個特別不行」
LICENSE_TRAPS = re.compile(r"^lib(av\w+|sw\w+|postproc|gst\w+|dc1394)[.-]")

# `cmake --install` 只裝了一部分授權檔（share/licenses/opencv5/）。下面這些也有靜態連進
# cv2.so，但 OpenCV 不一定會裝它們的授權檔（2026-09-28 對照 build/3rdparty/lib/*.a 查出來的），
# cmake 沒裝到的就從原始碼／建置目錄補。路徑相對 repo 根目錄；兩邊都沒有就失敗。
EXTRA_LICENSES = {
    "opencv-LICENSE": "opencv/LICENSE",                                          # Apache-2.0
    "libwebp-COPYING": "opencv/3rdparty/libwebp/COPYING",                        # BSD-3-Clause
    "ittnotify-BSD-3-Clause.txt": "opencv/3rdparty/ittnotify/src/ittnotify/BSD-3-Clause.txt",
    # Intel IPP（ippicv）：Intel Simplified Software License，允許散布，條件是附上這份授權
    "ippicv-EULA.txt": "build/3rdparty/ippicv/ippicv_lnx/EULA.txt",
    "ippicv-third-party-programs.txt": "build/3rdparty/ippicv/ippicv_lnx/third-party-programs.txt",
}


def die(msg: str) -> None:
    print(f"[wheel] ✗ {msg}", file=sys.stderr)
    sys.exit(1)


def ver_tuple(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.split("."))


def check_needed(so: Path) -> None:
    out = subprocess.run(["readelf", "-d", str(so)], capture_output=True, text=True, check=True).stdout
    needed = re.findall(r"\(NEEDED\)\s+Shared library: \[([^\]]+)\]", out)
    bad = [n for n in needed if not ALLOWED_NEEDED.match(n)]
    for n in bad:
        why = "（FFmpeg/GStreamer/libdc1394 系列，LGPL——這份建置就是為了不帶它）" if LICENSE_TRAPS.match(n) else \
              "（客戶機台不保證有，.deb 也沒宣告相依；請在 build.sh 改用 BUILD_xxx=ON 內建編進來）"
        print(f"[wheel] ✗ {so.name} 動態連結了 {n} {why}", file=sys.stderr)
    if bad:
        die("NEEDED 白名單沒過")
    print(f"[wheel] NEEDED：{', '.join(needed)} ✓")


def check_symbol_versions(so: Path) -> None:
    out = subprocess.run(["objdump", "-T", str(so)], capture_output=True, text=True, check=True).stdout
    for prefix, limit in (("GLIBC", MAX_GLIBC), ("GLIBCXX", MAX_GLIBCXX)):
        vers = set(re.findall(rf"\b{prefix}_(\d+(?:\.\d+)+)\b", out))
        if not vers:
            continue
        hi = max(vers, key=ver_tuple)
        if ver_tuple(hi) > ver_tuple(limit):
            syms = sorted({line.split()[-1] for line in out.splitlines() if f"{prefix}_{hi}" in line})
            die(f"{so.name} 需要 {prefix}_{hi}，超過目標基準 {limit}（例如：{', '.join(syms[:5])}）\n"
                f"        這份 .so 裝到 Ubuntu 22.04 會 ImportError。請在 ubuntu:22.04 容器裡編"
                f"（見 ci/build_in_container.sh）。")
        print(f"[wheel] {prefix} 最高 {hi} <= {limit} ✓")


def record_hash(data: bytes) -> str:
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
    return f"sha256={digest}"


def main() -> None:
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--stage", type=Path, default=here / "build" / "stage")
    ap.add_argument("--out", type=Path, default=here / "dist")
    args = ap.parse_args()

    if sys.version_info[:2] != (3, 12):
        die(f"要用目標 Python 3.12 跑（wheel 標籤取自執行的直譯器），目前是 {platform.python_version()}")

    stage: Path = args.stage
    cv2_dir = stage / "python" / "cv2"
    if not (cv2_dir / "__init__.py").is_file():
        die(f"找不到 {cv2_dir}/__init__.py——build.sh 的 cmake --install 有跑嗎？")
    sos = sorted(cv2_dir.rglob("*.so"))
    if len(sos) != 1:
        die(f"預期 cv2/ 底下剛好一個 .so（BUILD_SHARED_LIBS=OFF 靜態連結），實際 {len(sos)} 個：{sos}")
    so = sos[0]
    print(f"[wheel] 擴充模組：{so.relative_to(stage)}（{so.stat().st_size / 1e6:.1f} MB）")

    # config*.py 是 cmake 產生的，要確認沒有把 build 機的絕對路徑寫進去
    for cfg in cv2_dir.glob("config*.py"):
        text = cfg.read_text()
        if str(stage) in text or str(here) in text:
            die(f"{cfg.name} 裡有建置機的絕對路徑，裝到別台會找不到 .so：\n{text}")

    check_needed(so)
    check_symbol_versions(so)

    # 授權檔：cmake 裝的那批 + EXTRA_LICENSES 補的。這份 wheel 會被散布出去，授權檔不能少。
    lic_dir = stage / "share" / "licenses" / "opencv5"
    licenses: list[tuple[Path, str]] = []
    if lic_dir.is_dir():
        licenses += [(p, p.name) for p in sorted(lic_dir.iterdir()) if p.is_file()]
    if not licenses:
        die(f"{lic_dir} 是空的——cmake --install 沒裝到 3rdparty 授權檔？")
    for name, rel in EXTRA_LICENSES.items():
        if any(n == name for _, n in licenses):
            continue     # 有些版本/情況 cmake 自己會裝（ittnotify、ippicv），不要重複放
        src = here / rel
        if not src.is_file():
            die(f"找不到授權檔 {rel}（{name}）——對應的元件有靜態連進 cv2.so，授權檔一定要帶")
        licenses.append((src, name))

    arch = platform.machine()
    tag = f"cp312-cp312-linux_{arch}"
    dist_info = f"{DIST_NAME}-{VERSION}.dist-info"
    args.out.mkdir(parents=True, exist_ok=True)
    whl = args.out / f"{DIST_NAME}-{VERSION}-{tag}.whl"

    lic_names = [f"licenses/{name}" for _, name in licenses]
    metadata = "\n".join([
        "Metadata-Version: 2.4",
        f"Name: {PROJECT_NAME}",
        f"Version: {VERSION}",
        "Summary: OpenCV 5.0.0 主模組、不含 FFmpeg/GStreamer 的 headless 建置（lottery-reader / yolo-trainer 出貨用）",
        "License-Expression: Apache-2.0",
        *[f"License-File: {n}" for n in lic_names],
        "Requires-Python: ==3.12.*",
        "Requires-Dist: numpy>=2",
        "",
    ])
    wheel_meta = "\n".join([
        "Wheel-Version: 1.0",
        "Generator: opencv-nofmpeg-build/package_wheel.py",
        "Root-Is-Purelib: false",
        f"Tag: {tag}",
        "",
    ])

    records: list[str] = []

    def add(zf: zipfile.ZipFile, arcname: str, data: bytes, mode: int = 0o644) -> None:
        info = zipfile.ZipInfo(arcname, date_time=(2026, 1, 1, 0, 0, 0))   # 固定時間，同樣輸入產出同樣 wheel
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = (0o100000 | mode) << 16
        zf.writestr(info, data)
        records.append(f"{arcname},{record_hash(data)},{len(data)}")

    with tempfile.TemporaryDirectory() as tmp:
        # 發佈版把除錯符號剝掉（官方 wheel 也是），體積差很多；原檔留在 stage/ 不動
        so_data = so.read_bytes()
        if shutil.which("strip"):
            stripped = Path(tmp) / so.name
            subprocess.run(["strip", "--strip-unneeded", "-o", str(stripped), str(so)], check=True)
            so_data = stripped.read_bytes()
            print(f"[wheel] strip 後 {len(so_data) / 1e6:.1f} MB")

        tmp_whl = Path(tmp) / whl.name
        with zipfile.ZipFile(tmp_whl, "w") as zf:
            for p in sorted(cv2_dir.rglob("*")):
                if not p.is_file() or "__pycache__" in p.parts:
                    continue
                arc = f"cv2/{p.relative_to(cv2_dir).as_posix()}"
                if p == so:
                    add(zf, arc, so_data, 0o755)
                else:
                    add(zf, arc, p.read_bytes())
            for (p, _), n in zip(licenses, lic_names):
                add(zf, f"{dist_info}/{n}", p.read_bytes())
            add(zf, f"{dist_info}/METADATA", metadata.encode())
            add(zf, f"{dist_info}/WHEEL", wheel_meta.encode())
            add(zf, f"{dist_info}/top_level.txt", b"cv2\n")
            record_name = f"{dist_info}/RECORD"
            records.append(f"{record_name},,")
            info = zipfile.ZipInfo(record_name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, "\n".join(records) + "\n")
        shutil.move(tmp_whl, whl)

    print(f"[wheel] 完成：{whl}（{whl.stat().st_size / 1e6:.1f} MB，{len(records)} 個檔案，"
          f"含 {len(licenses)} 份授權檔）")


if __name__ == "__main__":
    main()
