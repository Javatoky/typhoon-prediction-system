"""可断点续传的 IBTrACS 下载脚本。

为什么需要它：IBTrACS 的 WP 子集约 110 MB，直接浏览器下载中断后要重来；
本脚本支持 Range 续传，并且只依赖标准库（不引入 requests 依赖）。

用法（在 backend 目录，激活虚拟环境后）：
    python scripts/fetch_ibtracs.py                    # 下载 WP 子集（默认）
    python scripts/fetch_ibtracs.py --list             # 只列出可用文件与大小
    python scripts/fetch_ibtracs.py --basin SI         # 下载其他盆地
    python scripts/fetch_ibtracs.py --force            # 忽略已有文件重新下载

数据来源：NOAA/NCEI IBTrACS v04r01 官方 CSV 目录（公开数据，无需鉴权）。
"""

from __future__ import annotations

import argparse
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BACKEND_DIR.parent
RAW_DIR = PROJECT_ROOT / "data" / "raw"

BASE_URL = (
    "https://www.ncei.noaa.gov/data/"
    "international-best-track-archive-for-climate-stewardship-ibtracs/"
    "v04r01/access/csv"
)
VERSION = "v04r01"
USER_AGENT = "Mozilla/5.0 (compatible; software-engineering-course-project/1.0)"
TIMEOUT_SECONDS = 60
CHUNK_SIZE = 1024 * 256


def target_name(basin: str) -> str:
    return f"ibtracs.{basin.upper()}.list.{VERSION}.csv"


def url_for(basin: str) -> str:
    return f"{BASE_URL}/{target_name(basin)}"


def remote_size(url: str) -> int | None:
    """用 HEAD 请求取得远端文件大小（不支持时返回 None）。"""
    request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            length = response.headers.get("Content-Length")
            return int(length) if length else None
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError):
        return None


def download(basin: str, *, force: bool = False) -> Path:
    url = url_for(basin)
    destination = RAW_DIR / target_name(basin)
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    if destination.exists() and force:
        print(f"删除已有文件：{destination.name}")
        destination.unlink()

    total = remote_size(url)
    if total is None:
        print("警告：无法获取远端文件大小，将直接开始下载（无法显示百分比）。")
    else:
        print(f"远端文件：{destination.name}（{total / 1024 / 1024:.1f} MB）")

    resume_at = destination.stat().st_size if destination.exists() else 0
    if total is not None and resume_at == total:
        print(f"文件已完整，跳过下载：{destination}")
        return destination
    if resume_at and total is not None and resume_at > total:
        print("本地文件比远端大，可能是残留内容，重新下载。")
        destination.unlink()
        resume_at = 0

    headers = {"User-Agent": USER_AGENT}
    mode = "wb"
    if resume_at:
        headers["Range"] = f"bytes={resume_at}-"
        mode = "ab"
        print(f"检测到未完成的文件，从 {resume_at / 1024 / 1024:.1f} MB 处续传。")

    request = urllib.request.Request(url, headers=headers)
    started = time.time()
    downloaded = resume_at
    last_report = 0.0

    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response, destination.open(
            mode
        ) as handle:
            while True:
                chunk = response.read(CHUNK_SIZE)
                if not chunk:
                    break
                handle.write(chunk)
                downloaded += len(chunk)
                now = time.time()
                if now - last_report >= 2.0:
                    last_report = now
                    if total:
                        percent = downloaded / total * 100
                        speed = (downloaded - resume_at) / max(now - started, 1e-6) / 1024 / 1024
                        print(
                            f"  {percent:5.1f}%  {downloaded / 1024 / 1024:7.1f} MB"
                            f" / {total / 1024 / 1024:.1f} MB  ({speed:.1f} MB/s)",
                            flush=True,
                        )
                    else:
                        print(f"  {downloaded / 1024 / 1024:.1f} MB", flush=True)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
        print(f"\n下载中断：{exc}")
        print(f"已下载 {downloaded / 1024 / 1024:.1f} MB，可重新执行本脚本继续（自动续传）。")
        return destination

    elapsed = time.time() - started
    final_size = destination.stat().st_size
    print(
        f"下载完成：{destination}\n"
        f"  大小 {final_size / 1024 / 1024:.1f} MB，本次耗时 {elapsed / 60:.1f} 分钟"
    )
    if total is not None and final_size != total:
        print(f"警告：文件大小 {final_size} 与远端 {total} 不一致，请重新执行以续传。")
    return destination


def list_files() -> None:
    print("IBTrACS v04r01 官方 CSV 目录可用文件（仅列出盆地与常用子集）：")
    print(f"  目录：{BASE_URL}/")
    for basin in ("WP", "EP", "NA", "NI", "SI", "SP", "SA", "ALL", "last3years", "since1980", "ACTIVE"):
        url = url_for(basin)
        size = remote_size(url)
        label = f"{size / 1024 / 1024:.1f} MB" if size else "大小未知"
        print(f"  {target_name(basin):38s} {label}")


def main() -> int:
    parser = argparse.ArgumentParser(description="下载 IBTrACS v04r01 子集数据")
    parser.add_argument("--basin", default="WP", help="盆地代码（WP/EP/NA/NI/SI/SP/SA/ALL 等），默认 WP")
    parser.add_argument("--force", action="store_true", help="忽略已有文件，重新下载")
    parser.add_argument("--list", action="store_true", help="只列出可用文件与大小")
    args = parser.parse_args()

    if args.list:
        list_files()
        return 0

    destination = download(args.basin, force=args.force)
    if not destination.exists() or destination.stat().st_size == 0:
        print("下载失败：文件不存在或为空。")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
