#!/usr/bin/env python3
"""產生 App 圖示。

    python3 scripts/make-icon.py

會覆寫 Resources/Assets.xcassets/AppIcon.appiconset/AppIcon.png（1024×1024）。

圖案是一段聲音波形 —— 中間高兩側低，跟 App 裡「跟著聲音走」這件事對上。
配色直接取自 Sources/Views/Theme.swift，圖示跟 App 內部才不會像兩套東西。

要調整就改下面的 BARS（每根長條的相對高度）再跑一次。
"""

import os

from PIL import Image, ImageDraw

SIZE = 1024

# 對齊 Theme.swift
TOP = (18, 22, 32)        # Theme.backdrop 的上緣
BOTTOM = (11, 13, 17)     # Theme.backdrop 的下緣
ACCENT = (232, 121, 74)   # Theme.accent

# 每根長條的高度，相對於圖示邊長。中間高、兩側低，像一段話的音量起伏。
BARS = [0.20, 0.38, 0.60, 0.44, 0.28]

BAR_WIDTH = 78
GAP = 46


def backdrop() -> Image.Image:
    """由上往下的深色漸層，跟 App 的背景同一個調子。"""
    image = Image.new("RGB", (SIZE, SIZE))
    draw = ImageDraw.Draw(image)
    for y in range(SIZE):
        t = y / (SIZE - 1)
        draw.line(
            [(0, y), (SIZE, y)],
            fill=tuple(round(TOP[i] + (BOTTOM[i] - TOP[i]) * t) for i in range(3)),
        )
    return image


def draw_bars(image: Image.Image) -> None:
    draw = ImageDraw.Draw(image)
    total = len(BARS) * BAR_WIDTH + (len(BARS) - 1) * GAP
    x = (SIZE - total) / 2
    radius = BAR_WIDTH / 2

    for ratio in BARS:
        height = SIZE * ratio
        top = (SIZE - height) / 2
        draw.rounded_rectangle(
            [x, top, x + BAR_WIDTH, top + height],
            radius=radius,
            fill=ACCENT,
        )
        x += BAR_WIDTH + GAP


def main() -> None:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    target = os.path.join(
        root, "Resources", "Assets.xcassets", "AppIcon.appiconset", "AppIcon.png"
    )

    image = backdrop()
    draw_bars(image)
    image.save(target, "PNG")
    print("已寫入 " + os.path.relpath(target, root))


if __name__ == "__main__":
    main()
