# -*- coding: utf-8 -*-
"""Generate Xiaohongshu beta recruitment carousel images for piia-engram."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Sequence

from PIL import Image, ImageDraw, ImageFont


WIDTH = 1080
HEIGHT = 1440

ROOT = Path(__file__).resolve().parent

COLORS = {
    "bg_top": "#0a1628",
    "bg_bottom": "#0b1622",
    "title": "#e8edf3",
    "accent": "#4da8da",
    "body": "#b8c9d9",
    "muted": "#556677",
    "highlight": "#58a6ff",
    "panel": (30, 60, 114, 76),
    "panel_edge": "#30363d",
    "pill": "#1f3a5f",
}


FONT_CANDIDATES = {
    "zh_regular": [
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        "C:/Windows/Fonts/simsun.ttc",
    ],
    "zh_bold": [
        "C:/Windows/Fonts/msyhbd.ttc",
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
    ],
    "latin_regular": [
        "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/msyh.ttc",
    ],
    "mono": [
        "C:/Windows/Fonts/consola.ttf",
        "C:/Windows/Fonts/cour.ttf",
        "C:/Windows/Fonts/msyh.ttc",
    ],
}


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))


def font(kind: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in FONT_CANDIDATES[kind]:
        path = Path(candidate)
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def make_canvas() -> Image.Image:
    top = hex_to_rgb(COLORS["bg_top"])
    bottom = hex_to_rgb(COLORS["bg_bottom"])
    img = Image.new("RGB", (WIDTH, HEIGHT), top)
    draw = ImageDraw.Draw(img)
    for y in range(HEIGHT):
        ratio = y / (HEIGHT - 1)
        color = tuple(round(top[i] * (1 - ratio) + bottom[i] * ratio) for i in range(3))
        draw.line([(0, y), (WIDTH, y)], fill=color)

    # Subtle brand texture: quiet scan lines and a soft diagonal glint.
    for y in range(0, HEIGHT, 36):
        draw.line([(72, y), (WIDTH - 72, y)], fill=(15, 32, 51), width=1)
    overlay = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    odraw = ImageDraw.Draw(overlay)
    odraw.polygon([(0, 1120), (WIDTH, 930), (WIDTH, 1030), (0, 1220)], fill=(88, 166, 255, 16))
    return Image.alpha_composite(img.convert("RGBA"), overlay)


def text_size(draw: ImageDraw.ImageDraw, text: str, text_font: ImageFont.ImageFont) -> tuple[int, int]:
    box = draw.textbbox((0, 0), text, font=text_font)
    return box[2] - box[0], box[3] - box[1]


def draw_centered(
    draw: ImageDraw.ImageDraw,
    y: int,
    text: str,
    text_font: ImageFont.ImageFont,
    fill: str | tuple[int, int, int],
    x_center: int = WIDTH // 2,
) -> tuple[int, int, int, int]:
    box = draw.textbbox((0, 0), text, font=text_font)
    w = box[2] - box[0]
    x = x_center - w // 2
    draw.text((x, y), text, font=text_font, fill=fill)
    return draw.textbbox((x, y), text, font=text_font)


def draw_pill(
    draw: ImageDraw.ImageDraw,
    text: str,
    y: int,
    text_font: ImageFont.ImageFont,
    fill: str,
    text_fill: str,
    pad_x: int,
    pad_y: int,
    outline: str | None = None,
) -> tuple[int, int, int, int]:
    tw, th = text_size(draw, text, text_font)
    x0 = (WIDTH - tw - 2 * pad_x) // 2
    y0 = y
    x1 = x0 + tw + 2 * pad_x
    y1 = y0 + th + 2 * pad_y
    draw.rounded_rectangle([x0, y0, x1, y1], radius=(y1 - y0) // 2, fill=fill, outline=outline, width=2)
    draw.text((x0 + pad_x, y0 + pad_y - 2), text, font=text_font, fill=text_fill)
    return x0, y0, x1, y1


def draw_panel(
    img: Image.Image,
    xy: Sequence[int],
    radius: int = 24,
    outline: str = COLORS["panel_edge"],
) -> ImageDraw.ImageDraw:
    overlay = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    odraw = ImageDraw.Draw(overlay)
    odraw.rounded_rectangle(xy, radius=radius, fill=COLORS["panel"], outline=outline, width=2)
    img.alpha_composite(overlay)
    return ImageDraw.Draw(img)


def save(img: Image.Image, name: str) -> None:
    img.convert("RGB").save(ROOT / name, optimize=True)


def slide_1_cover() -> None:
    img = make_canvas()
    draw = ImageDraw.Draw(img)

    draw_pill(draw, "v3.29 内测", 118, font("zh_bold", 36), COLORS["pill"], COLORS["highlight"], 34, 18)
    draw_centered(draw, 280, "piia-engram", font("zh_bold", 96), COLORS["title"])
    draw_centered(draw, 420, "让 AI 记住你是谁", font("zh_regular", 52), COLORS["accent"])

    bullet_font = font("zh_regular", 40)
    for y, text in zip(
        [582, 668, 754],
        ["● 跨工具共享记忆", "● 数据 100% 本地存储", "● 你审批，AI 才能记"],
    ):
        draw_centered(draw, y, text, bullet_font, COLORS["body"])

    draw_pill(draw, "5 个内测名额", 1000, font("zh_bold", 44), COLORS["pill"], COLORS["highlight"], 48, 22)
    draw_centered(draw, 1092, "名额满即关闭", font("zh_regular", 32), COLORS["muted"])
    draw_centered(draw, 1304, "github.com/Patdolitse/piia-engram", font("mono", 28), COLORS["muted"])

    save(img, "slide_1_cover.png")


def feature_card(img: Image.Image, top: int, number: str, title: str, desc: str) -> None:
    left = 90
    right = WIDTH - 90
    height = 260
    draw = draw_panel(img, [left, top, right, top + height])
    draw.text((left + 54, top + 58), number, font=font("zh_bold", 56), fill=COLORS["highlight"])
    draw.text((left + 176, top + 64), title, font=font("zh_bold", 44), fill=COLORS["title"])
    draw.text((left + 176, top + 136), desc, font=font("zh_regular", 34), fill=COLORS["body"])


def slide_2_features() -> None:
    img = make_canvas()
    draw = ImageDraw.Draw(img)

    draw_centered(draw, 100, "它能做什么？", font("zh_bold", 48), COLORS["accent"])
    feature_card(img, 250, "01", "跨工具记忆", "Cursor 里教过的偏好，Claude Code 也知道")
    feature_card(img, 580, "02", "本地存储", "所有数据存在你电脑上，不走云端")
    feature_card(img, 910, "03", "你说了算", "AI 想记的东西先进待审区，你确认才生效")

    draw = ImageDraw.Draw(img)
    draw_centered(draw, 1280, "开源 · Apache 2.0 · MCP 协议", font("zh_regular", 32), COLORS["muted"])

    save(img, "slide_2_features.png")


def join_step(img: Image.Image, top: int, number: str, title: str, desc: str) -> None:
    draw = ImageDraw.Draw(img)
    circle_x = 175
    circle_y = top + 40
    diameter = 80
    draw.ellipse(
        [circle_x, circle_y, circle_x + diameter, circle_y + diameter],
        fill=COLORS["highlight"],
    )
    num_font = font("zh_bold", 40)
    tw, th = text_size(draw, number, num_font)
    draw.text(
        (circle_x + diameter // 2 - tw // 2, circle_y + diameter // 2 - th // 2 - 4),
        number,
        font=num_font,
        fill=COLORS["bg_bottom"],
    )
    text_x = 300
    draw.text((text_x, top + 30), title, font=font("zh_bold", 44), fill=COLORS["title"])
    draw.text((text_x, top + 104), desc, font=font("zh_regular", 34), fill=COLORS["body"])
    draw.line([(text_x, top + 174), (WIDTH - 160, top + 174)], fill=(48, 54, 61), width=1)


def slide_3_join() -> None:
    img = make_canvas()
    draw = ImageDraw.Draw(img)

    draw_centered(draw, 100, "如何参与内测？", font("zh_bold", 48), COLORS["accent"])
    join_step(img, 280, "1", "私信我", "留下你用的 AI 工具和系统")
    join_step(img, 560, "2", "装上跑起来", "发你安装说明，5 分钟搞定")
    join_step(img, 840, "3", "用几天，反馈", "一行命令生成报告，不含个人信息")

    cta_box = [150, 1138, WIDTH - 150, 1244]
    draw.rounded_rectangle(cta_box, radius=24, fill=COLORS["pill"], outline=COLORS["highlight"], width=2)
    draw_centered(draw, 1166, "私信「想参与内测」即可", font("zh_bold", 44), COLORS["title"])
    draw_centered(draw, 1300, "需要：Python 3.10+ · Claude Code / Cursor / Codex", font("zh_regular", 28), COLORS["muted"])

    save(img, "slide_3_join.png")


def main() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    slide_1_cover()
    slide_2_features()
    slide_3_join()
    for name in ["slide_1_cover.png", "slide_2_features.png", "slide_3_join.png"]:
        with Image.open(ROOT / name) as img:
            if img.size != (WIDTH, HEIGHT):
                raise RuntimeError(f"{name} size wrong: {img.size}")
        print(f"OK: {name}")


if __name__ == "__main__":
    main()
