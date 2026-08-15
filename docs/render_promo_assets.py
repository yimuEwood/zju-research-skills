"""Render reproducible promotional cards for ZJU Research OS.

All quantitative claims below are copied from the public release manifests and
legacy evaluation summaries.  The cards deliberately distinguish historical
internal evidence from current engineering checks and qualitative comparisons.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from PIL import Image, ImageDraw, ImageFilter, ImageFont


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "docs" / "assets"
W, H = 1080, 1350

NAVY = (5, 15, 40)
NAVY_2 = (10, 31, 67)
PANEL = (17, 40, 76)
PANEL_2 = (22, 51, 91)
WHITE = (244, 249, 255)
MUTED = (169, 193, 219)
CYAN = (42, 211, 255)
BLUE = (63, 126, 255)
PURPLE = (156, 112, 255)
GOLD = (255, 190, 91)
GREEN = (69, 222, 160)
RED = (255, 111, 126)

FONT_REGULAR = Path("C:/Windows/Fonts/msyh.ttc")
FONT_BOLD = Path("C:/Windows/Fonts/msyhbd.ttc")


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_BOLD if bold else FONT_REGULAR), size)


def linear_gradient(top: tuple[int, int, int], bottom: tuple[int, int, int]) -> Image.Image:
    image = Image.new("RGB", (W, H), top)
    pixels = image.load()
    for y in range(H):
        t = y / max(H - 1, 1)
        color = tuple(round(a * (1 - t) + b * t) for a, b in zip(top, bottom))
        for x in range(W):
            pixels[x, y] = color
    return image


def glow(image: Image.Image, xy: tuple[int, int], radius: int, color: tuple[int, int, int], alpha: int = 110) -> None:
    layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    x, y = xy
    draw.ellipse((x - radius // 3, y - radius // 3, x + radius // 3, y + radius // 3), fill=(*color, alpha))
    layer = layer.filter(ImageFilter.GaussianBlur(radius // 2))
    image.alpha_composite(layer)


def wrap_chars(draw: ImageDraw.ImageDraw, text: str, fnt: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for char in text:
        candidate = current + char
        width = draw.textbbox((0, 0), candidate, font=fnt)[2]
        if current and width > max_width:
            lines.append(current)
            current = char
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def text_block(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    fnt: ImageFont.FreeTypeFont,
    fill: tuple[int, int, int] | tuple[int, int, int, int],
    max_width: int,
    spacing: int = 12,
) -> int:
    x, y = xy
    for line in wrap_chars(draw, text, fnt, max_width):
        draw.text((x, y), line, font=fnt, fill=fill)
        box = draw.textbbox((x, y), line, font=fnt)
        y = box[3] + spacing
    return y


def rounded_panel(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    fill: tuple[int, int, int] | tuple[int, int, int, int] = PANEL,
    outline: tuple[int, int, int] | tuple[int, int, int, int] = (45, 86, 130),
    radius: int = 28,
    width: int = 2,
) -> None:
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def label(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, color: tuple[int, int, int] = CYAN) -> None:
    x, y = xy
    fnt = font(24, True)
    bbox = draw.textbbox((0, 0), text, font=fnt)
    width = bbox[2] - bbox[0] + 34
    # Draw an opaque dark capsule.  Semi-transparent pixels written directly
    # onto an RGBA canvas lose their intended blend when exported to RGB.
    draw.rounded_rectangle((x, y, x + width, y + 43), radius=21, fill=(12, 48, 78, 255), outline=(*color, 255), width=2)
    draw.text((x + 17, y + 7), text, font=fnt, fill=color)


def footer(draw: ImageDraw.ImageDraw, page: str) -> None:
    draw.line((70, 1285, 1010, 1285), fill=(55, 87, 124), width=2)
    draw.text((70, 1303), "ZJU Research OS · Apache-2.0 · 社区开源项目，非浙江大学官方服务", font=font(21), fill=MUTED)
    bbox = draw.textbbox((0, 0), page, font=font(21, True))
    draw.text((1010 - (bbox[2] - bbox[0]), 1303), page, font=font(21, True), fill=CYAN)


def cover_crop(source: Image.Image) -> Image.Image:
    scale = max(W / source.width, H / source.height)
    resized = source.resize((round(source.width * scale), round(source.height * scale)), Image.Resampling.LANCZOS)
    left = (resized.width - W) // 2
    top = (resized.height - H) // 2
    return resized.crop((left, top, left + W, top + H))


def render_hero() -> None:
    background = cover_crop(Image.open(ASSETS / "hero-background.png").convert("RGB")).convert("RGBA")

    # Make the text zones calm without hiding the generated research scene.
    veil = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    vp = veil.load()
    for y in range(H):
        top_alpha = max(0, 225 - int(y * 0.56)) if y < 500 else 0
        bottom_alpha = max(0, int((y - 1010) * 0.50)) if y > 1010 else 0
        alpha = min(210, max(top_alpha, bottom_alpha, 34))
        for x in range(W):
            vp[x, y] = (2, 11, 35, alpha)
    background.alpha_composite(veil)
    draw = ImageDraw.Draw(background, "RGBA")

    label(draw, (70, 64), "OPEN SOURCE · CHINESE FIRST")
    draw.text((70, 135), "ZJU", font=font(104, True), fill=WHITE)
    draw.text((70, 235), "Research OS", font=font(104, True), fill=CYAN)
    draw.text((73, 354), "面向浙大师生场景的科研工作流蒸馏版", font=font(35, True), fill=WHITE)
    draw.text((73, 411), "1 个 Director 统筹 19 个专业 Skills", font=font(29), fill=MUTED)

    metrics = [("20", "Skills"), ("4", "领域模板"), ("103", "自动化测试")]
    x_positions = [70, 383, 696]
    for x, (value, caption) in zip(x_positions, metrics):
        rounded_panel(draw, (x, 1048, x + 274, 1215), fill=(8, 26, 58, 210), outline=(66, 160, 209, 180), radius=25)
        draw.text((x + 22, 1065), value, font=font(58, True), fill=CYAN)
        draw.text((x + 22, 1142), caption, font=font(25, True), fill=WHITE)
    draw.text((70, 1243), "不是把 Skill 堆在一起，而是让检索、实验、分析、写作与转化共享同一条证据链。", font=font(24), fill=WHITE)
    background.convert("RGB").save(ASSETS / "hero.png", quality=95)


def base_card() -> Image.Image:
    image = linear_gradient(NAVY, NAVY_2).convert("RGBA")
    glow(image, (900, 160), 500, BLUE, 80)
    glow(image, (120, 1120), 420, PURPLE, 55)
    return image


def render_architecture() -> None:
    image = base_card()
    draw = ImageDraw.Draw(image, "RGBA")
    label(draw, (70, 58), "ARCHITECTURE")
    draw.text((70, 125), "从“技能集合”到", font=font(48, True), fill=WHITE)
    draw.text((70, 187), "科研操作系统", font=font(68, True), fill=CYAN)
    draw.text((72, 279), "一个共享 Research Mission，把 19 个专业能力编成可验证的任务图。", font=font(27), fill=MUTED)

    # Director node.
    rounded_panel(draw, (270, 352, 810, 465), fill=(25, 57, 103, 245), outline=CYAN, radius=30, width=3)
    draw.ellipse((300, 379, 359, 438), fill=(*CYAN, 42), outline=CYAN, width=3)
    draw.text((318, 386), "D", font=font(30, True), fill=WHITE)
    draw.text((384, 371), "Research Director", font=font(36, True), fill=WHITE)
    draw.text((384, 416), "路由 · 拆解 · 协调 · 升级", font=font(23), fill=MUTED)

    # Mission bus.
    draw.line((540, 465, 540, 516), fill=CYAN, width=4)
    rounded_panel(draw, (100, 516, 980, 638), fill=(10, 36, 74, 245), outline=BLUE, radius=26, width=3)
    draw.text((130, 540), "SHARED RESEARCH MISSION", font=font(25, True), fill=BLUE)
    mission_items = ["目标/范围", "证据锚点", "产物状态", "权限等级", "开放问题"]
    x = 130
    for item in mission_items:
        bw = draw.textbbox((0, 0), item, font=font(23, True))[2] + 30
        draw.rounded_rectangle((x, 584, x + bw, 622), radius=18, fill=(32, 68, 112), outline=(68, 126, 187), width=1)
        draw.text((x + 15, 589), item, font=font(23, True), fill=WHITE)
        x += bw + 14

    draw.line((540, 638, 540, 687), fill=BLUE, width=4)
    # Specialist clusters.
    cluster_data = [
        ("发现与获取", "检索 · 监测 · 全文 · 阅读", CYAN),
        ("分析与实验", "假设 · 日志 · 统计 · 化学", GREEN),
        ("表达与交付", "写作 · 图表 · PPT · 专利", PURPLE),
        ("审查与交付", "引用 · 审稿 · 回复 · 数据 · 诚信", GOLD),
    ]
    boxes = [(70, 687, 520, 813), (560, 687, 1010, 813), (70, 843, 520, 969), (560, 843, 1010, 969)]
    for box, (title, detail, color) in zip(boxes, cluster_data):
        rounded_panel(draw, box, fill=(18, 42, 79, 235), outline=(*color, 190), radius=24)
        draw.rectangle((box[0], box[1] + 22, box[0] + 7, box[3] - 22), fill=color)
        draw.text((box[0] + 28, box[1] + 21), title, font=font(30, True), fill=WHITE)
        draw.text((box[0] + 28, box[1] + 71), detail, font=font(22), fill=MUTED)

    # Cross-stage capability layer.
    draw.line((540, 969, 540, 1015), fill=PURPLE, width=4)
    rounded_panel(draw, (100, 1015, 980, 1155), fill=(20, 39, 69, 245), outline=(*GREEN, 190), radius=26, width=3)
    draw.text((130, 1038), "跨阶段能力接口", font=font(30, True), fill=GREEN)
    draw.text((130, 1087), "Paper Spine  ×  分析契约  ×  结果注册表  ×  转化图谱", font=font(25, True), fill=WHITE)
    draw.text((130, 1125), "同一结果可追溯到证据、实验设计、图件、答审与数据交付", font=font(22), fill=MUTED)

    draw.text((70, 1207), "核心变化：每个 Skill 不再各自“忘记上下文”，产物、证据和未决问题都回写同一任务状态。", font=font(23), fill=WHITE)
    footer(draw, "02 / 04")
    image.convert("RGB").save(ASSETS / "architecture.png", quality=95)


def render_benchmark() -> None:
    image = base_card()
    draw = ImageDraw.Draw(image, "RGBA")
    label(draw, (70, 58), "LEGACY V1 · INTERNAL")
    draw.text((70, 126), "第一批 7 Skills", font=font(50, True), fill=WHITE)
    draw.text((70, 191), "历史内部评测快照", font=font(64, True), fill=CYAN)
    draw.text((72, 281), "无 Skill / 固定 Nature / 蒸馏版；内部模型 rubric 分差，不是正确率或第三方认证。", font=font(24), fill=MUTED)

    draw.rounded_rectangle((70, 326, 1010, 372), radius=20, fill=(17, 55, 88), outline=(*GREEN, 180), width=2)
    draw.text((92, 334), "总体对固定 Nature：质量 +23.49 分  ·  中位耗时 −7.59%  ·  Token −24.84%", font=font(22, True), fill=GREEN)

    gains = [
        ("引用核验", 30.124),
        ("科研写作", 28.125),
        ("文献检索", 26.063),
        ("实验日志", 24.437),
        ("全文获取", 22.187),
        ("统计审计", 10.687),
        ("论文阅读*", 9.938),
    ]
    chart_left, chart_top, chart_width = 235, 386, 700
    max_gain = 32.0
    for idx, (name, gain) in enumerate(gains):
        y = chart_top + idx * 94
        draw.text((70, y + 8), name, font=font(25, True), fill=WHITE)
        draw.rounded_rectangle((chart_left, y + 8, chart_left + chart_width, y + 48), radius=20, fill=(33, 57, 91))
        bar_width = round(chart_width * gain / max_gain)
        color = CYAN if idx < 3 else BLUE if idx < 6 else PURPLE
        draw.rounded_rectangle((chart_left, y + 8, chart_left + bar_width, y + 48), radius=20, fill=color)
        value_text = f"+{gain:.2f} 分"
        bbox = draw.textbbox((0, 0), value_text, font=font(23, True))
        value_x = min(chart_left + bar_width + 12, 1000 - (bbox[2] - bbox[0]))
        draw.text((value_x, y + 12), value_text, font=font(23, True), fill=WHITE)

    # Summary cards.
    cards = [("70", "仓库案例"), ("210", "模型回答"), ("7", "重复 Pilot"), ("20", "工具偏差")]
    card_x = [70, 310, 550, 790]
    for x, (value, caption) in zip(card_x, cards):
        rounded_panel(draw, (x, 1060, x + 210, 1190), fill=(15, 43, 80, 245), outline=(53, 101, 148), radius=22)
        draw.text((x + 18, 1074), value, font=font(39, True), fill=GREEN)
        draw.text((x + 18, 1132), caption, font=font(19, True), fill=WHITE)

    draw.text((70, 1204), "限制：位置未区组均衡，工具策略未由执行器强制，完整 raw records 未公开。", font=font(20), fill=GOLD)
    draw.text((70, 1234), "仅作 legacy-v1 方向性证据；能力评测仍为 Beta，尚无 protocol v3 正式分。", font=font(20, True), fill=WHITE)
    footer(draw, "03 / 04")
    image.convert("RGB").save(ASSETS / "benchmark.png", quality=95)


def render_comparison() -> None:
    image = base_card()
    draw = ImageDraw.Draw(image, "RGBA")
    label(draw, (70, 58), "DISTILLATION + ZJU")
    draw.text((70, 126), "取长补短，不是简单拼盘", font=font(56, True), fill=WHITE)
    draw.text((72, 210), "吸收不同开源项目的强项，再用统一交接协议和本地科研场景重构。", font=font(27), fill=MUTED)

    rows = [
        ("Nature Skills", "中文科研工作流", "发现—分析—表达—转化闭环"),
        ("K-Dense", "数据库、统计、确定性脚本", "分析契约 + 结果单一事实源"),
        ("Auto pipelines", "长流程自动执行", "可暂停 Mission + 证据化交接"),
        ("Scholar / Supervisor", "质检与导师式审查", "异议—修改—验证闭环"),
        ("PaperSpine", "论文/提案阶段骨架", "扩展到冲突综合与判别实验"),
    ]
    rounded_panel(draw, (70, 294, 1010, 722), fill=(13, 37, 72, 245), outline=(51, 102, 151), radius=26)
    draw.text((96, 318), "来源类型", font=font(23, True), fill=CYAN)
    draw.text((330, 318), "吸收的长处", font=font(23, True), fill=CYAN)
    draw.text((665, 318), "本项目的系统化改造", font=font(23, True), fill=CYAN)
    draw.line((94, 360, 986, 360), fill=(64, 101, 139), width=2)
    for idx, (source, strength, upgrade) in enumerate(rows):
        y = 379 + idx * 66
        if idx % 2 == 0:
            draw.rounded_rectangle((88, y - 8, 992, y + 48), radius=12, fill=(26, 54, 91, 150))
        draw.text((98, y), source, font=font(21, True), fill=WHITE)
        draw.text((330, y), strength, font=font(20), fill=MUTED)
        draw.text((665, y), upgrade, font=font(20, True), fill=WHITE)
    draw.text((95, 684), "注：这是架构取向对照，不是跨仓库同题跑分或热度排名。", font=font(20), fill=GOLD)

    draw.text((70, 762), "对浙大师生的场景优势", font=font(40, True), fill=CYAN)
    advantages = [
        ("校外访问路由", "CARSI · WebVPN · RVPN；失败时给出合规人工路径"),
        ("常用资源导航", "CNKI · WoS · Scopus · SciFinder · Reaxys"),
        ("本土学科模板", "中文优先；覆盖化学、材料、生医与农业场景"),
        ("跨产物一致性", "统计结果一次登记，正文 · 图件 · 答审 · 数据共同引用"),
    ]
    boxes = [(70, 825, 520, 945), (560, 825, 1010, 945), (70, 975, 520, 1095), (560, 975, 1010, 1095)]
    for box, (title, detail) in zip(boxes, advantages):
        rounded_panel(draw, box, fill=(17, 43, 78, 240), outline=(45, 105, 153), radius=22)
        draw.ellipse((box[0] + 18, box[1] + 23, box[0] + 48, box[1] + 53), fill=(*GREEN, 40), outline=GREEN, width=2)
        draw.line((box[0] + 27, box[1] + 38, box[0] + 34, box[1] + 45), fill=GREEN, width=3)
        draw.line((box[0] + 34, box[1] + 45, box[0] + 43, box[1] + 31), fill=GREEN, width=3)
        draw.text((box[0] + 60, box[1] + 17), title, font=font(25, True), fill=WHITE)
        text_block(draw, (box[0] + 20, box[1] + 63), detail, font(18), MUTED, box[2] - box[0] - 40, spacing=4)

    rounded_panel(draw, (70, 1130, 1010, 1235), fill=(14, 45, 76, 230), outline=(*CYAN, 155), radius=22)
    draw.text((96, 1151), "已核验快照", font=font(24, True), fill=CYAN)
    draw.text((250, 1151), "20/20 Skill 校验 · 103 测试通过 · 39 个离线处理/验证脚本", font=font(22, True), fill=WHITE)
    draw.text((96, 1192), "浙大链接与制度文本核验日期：2026-08-11；建议每季度复核。", font=font(20), fill=MUTED)
    footer(draw, "04 / 04")
    image.convert("RGB").save(ASSETS / "comparison.png", quality=95)


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    render_hero()
    render_architecture()
    render_benchmark()
    render_comparison()
    for name in ("hero.png", "architecture.png", "benchmark.png", "comparison.png"):
        path = ASSETS / name
        print(f"rendered {path.relative_to(ROOT)} ({path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
