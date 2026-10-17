# -*- coding: utf-8 -*-
"""
生成应用图标 assets/app.ico（一次性开发工具，产物入库，运行时不依赖 Pillow）。

图形语义：深色圆角底 + 一对反向箭头（表示"在两套网络配置之间切换"）。

用法：
    python build/make_icon.py
依赖：Pillow（仅开发期需要，不写入 requirements.txt）
"""

import os
import sys

from PIL import Image, ImageDraw

SIZES = (256, 128, 64, 48, 32, 16)
BG = (26, 34, 46, 255)        # 深板岩色底
FG = (61, 220, 151, 255)      # 强调色（青绿）
HINT = (140, 160, 180, 255)   # 次级色


def _arrow(draw, x0, y0, x1, y1, thickness, color, rightwards=True):
    """画一条水平箭头（两端带箭头三角）。"""
    mid_y = (y0 + y1) // 2
    head = max(thickness * 2, thickness + 1)
    if rightwards:
        draw.rectangle([x0, mid_y - thickness // 2, x1 - head, mid_y + thickness // 2],
                       fill=color)
        draw.polygon([(x1, mid_y), (x1 - head, mid_y - head), (x1 - head, mid_y + head)],
                     fill=color)
    else:
        draw.rectangle([x0 + head, mid_y - thickness // 2, x1, mid_y + thickness // 2],
                       fill=color)
        draw.polygon([(x0, mid_y), (x0 + head, mid_y - head), (x0 + head, mid_y + head)],
                     fill=color)


def build_image(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    radius = max(2, int(size * 0.22))
    draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=BG)

    pad = max(2, int(size * 0.16))
    thickness = max(1, int(size * 0.09))
    gap = max(1, int(size * 0.10))

    upper_top = pad
    upper_bottom = pad + thickness * 2
    lower_bottom = size - pad
    lower_top = lower_bottom - thickness * 2

    _arrow(draw, pad, upper_top, size - pad, upper_bottom - gap,
           thickness, FG, rightwards=True)
    _arrow(draw, pad, lower_top + gap, size - pad, lower_bottom,
           thickness, HINT, rightwards=False)
    return img


def main() -> int:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out_dir = os.path.join(root, "assets")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "app.ico")

    base = build_image(256)
    base.save(out_path, format="ICO",
              sizes=[(s, s) for s in SIZES])
    print(f"已生成: {out_path} ({os.path.getsize(out_path)} bytes, 尺寸 {list(SIZES)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
