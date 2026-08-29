#!/usr/bin/env python3
"""打包自包含审核器（零依赖），方便分发给多人离线使用。

用法::

    python scripts/build_review_kit.py \\
        --files data/annotations/P001/P001_A001_annotation_draft_2_translated.json \\
        [--files 另一份.json ...] [--out review_kit] [--zip]

产出 ``review_kit/`` 文件夹（可直接 zip 分发）：

    review_server.py          审核器后端（零依赖，内嵌失真分类）
    review_ui/index.html      审核器前端（单文件，无外部资源）
    start_review.bat          双击一键启动
    data/annotations/*.json   评测文件（按 --files 拷贝）
    README.txt                使用说明

纯标准库（shutil/zipfile），不依赖 hallu/api_client/.env/API key。
"""

from __future__ import annotations

import argparse
import shutil
import sys
import zipfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent

KIT_BAT = """@echo off
chcp 65001 >nul
cd /d "%~dp0"
python review_server.py
pause
"""

README = """植物科学科普文本信息失真标注 — 审核器使用说明

一、准备（仅第一次）
  1. 安装 Python 3.8 及以上版本，安装时勾选 "Add Python to PATH"。
  2. 解压本文件夹到任意位置。

二、开始审核
  1. 双击 start_review.bat。
  2. 命令行列出可审核文件，输入序号回车，浏览器自动打开审核界面。
  3. 在界面右上角填写你的姓名（审核人）。
  4. 逐条审核：看观点句 + 召回证据 + AI 分析 + 右侧失真细则，
     选择失真类型 / 严重度 / 未覆盖现象，填写意见，勾选
     "标记已完成审核"，点"保存"。
  5. 快捷键：← → 翻页，S 保存，1-9 选失真类型，D 标记完成。

三、多人协作（文件传递，全程无需联网）
  1. 审核结果直接写回 data/annotations/ 下的评测文件
     （human_reviews 字段，按审核人分列）。
  2. 一人审完后，把整个文件夹（或 data/annotations/ 下的评测文件）
     发给下一位审核人。
  3. 下一位打开时，界面顶部"他人审核参考"会显示前人结论；
     若有异议，填写自己的意见即可（互不覆盖）。

四、数据流转约定
  - 谁是审核人就以谁的名字保存，结果按名字分开，便于双人比对。
  - 审核完把评测文件回传给组长，用 human_reviews 汇总。
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="打包自包含审核器")
    parser.add_argument("--files", nargs="+", required=True, help="评测文件路径（可多个）")
    parser.add_argument("--out", default="review_kit", help="输出目录（默认 review_kit）")
    parser.add_argument("--zip", action="store_true", help="打包为 zip")
    args = parser.parse_args()

    out = ROOT / args.out
    if out.exists():
        shutil.rmtree(out)
    (out / "review_ui").mkdir(parents=True)
    (out / "data" / "annotations").mkdir(parents=True)

    # 拷贝审核器本体
    shutil.copy2(SCRIPT_DIR / "review_server.py", out / "review_server.py")
    shutil.copy2(SCRIPT_DIR / "review_ui" / "index.html", out / "review_ui" / "index.html")
    (out / "start_review.bat").write_text(KIT_BAT, encoding="utf-8")
    (out / "README.txt").write_text(README, encoding="utf-8-sig")

    # 拷贝评测文件（保持原文件名，放到 data/annotations/ 供 discover 扫描）
    copied = 0
    for f in args.files:
        p = Path(f)
        if not p.is_absolute():
            p = ROOT / p
        p = p.resolve()
        if not p.exists():
            print("警告：找不到评测文件，跳过: %s" % p, file=sys.stderr)
            continue
        shutil.copy2(p, out / "data" / "annotations" / p.name)
        print("  + %s" % p.name)
        copied += 1

    if copied == 0:
        print("错误：没有成功拷贝任何评测文件。", file=sys.stderr)
        return 1

    print("\n审核器已打包到: %s" % out)

    if args.zip:
        zip_path = out.with_suffix(".zip")
        if zip_path.exists():
            zip_path.unlink()
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in sorted(out.rglob("*")):
                zf.write(f, f.relative_to(out))
        print("已压缩: %s" % zip_path)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
