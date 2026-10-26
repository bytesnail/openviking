#!/usr/bin/env python3
"""生成长文本测试夹具(写入 tests/fixtures/longtext/,不入 git,测后删除)。

每个尺寸生成一篇结构化中文长文:多章节、段落内容有变化(避免退化成
重复文本),并在头部 ~2%、中部 ~50%、尾部 ~98% 三个位置植入唯一可检索
标记(形如 LT{size}-HEAD/MID/TAIL-{hex}),用于验证超长文档的深层检索。

用法:python3 tests/gen_longtext_fixture.py [尺寸字符数 ...](默认 50000 1000000 10000000 20000000)
"""
import random
import sys
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parent / "fixtures" / "longtext"

TOPICS = ["水系治理", "轨道动力学", "晶格热导", "季风气候", "信号完整性", "拓扑绝缘",
          "燃料电池", "流域生态", "光学镀膜", "噪声抑制", "结构疲劳", "菌群代谢"]
VERBS = ["决定了", "约束着", "影响着", "重塑了", "标记出", "耦合了"]
ADJS = ["缓慢的", "急剧的", "周期性的", "非线性的", "可逆的", "弥散的"]
OBJS = ["边界层", "相干态", "迁移率", "稳态点", "耦合系数", "衰减长度"]


def para(rng: random.Random, i: int) -> str:
    body = "。".join(
        f"在第{i}段中,{rng.choice(TOPICS)}的{rng.choice(ADJS)}{rng.choice(OBJS)}"
        f"{rng.choice(VERBS)}系统整体的{rng.choice(OBJS)}"
        for _ in range(rng.randint(3, 6))
    )
    return body + "。"


def marker(size: int, pos: str) -> str:
    tag = f"LT{size}-{pos}-7C4F{size % 97:02X}"
    return f"独特事实:{tag} 号信标于 2026 年在长文本测试场完成校准,校准码 {tag}-MX。"


def gen(size: int) -> Path:
    rng = random.Random(20261026 + size)
    chars = 0
    lines = [f"# 长文本测试文档 {size} 字符", "", marker(size, "HEAD"), ""]
    i = 0
    while chars < size:
        lines.append(f"## 第{i}章 {rng.choice(TOPICS)}的{rng.choice(ADJS)}行为")
        lines.append("")
        chap = []
        for _ in range(rng.randint(3, 6)):
            chap.append(para(rng, i))
            chap.append("")
        block = "\n".join(chap)
        # 在接近中部时植入中部标记
        if chars < size * 0.5 <= chars + len(block) + 40:
            mid = [marker(size, "MID"), ""]
            block_mid = "\n".join(mid) + block
            lines.extend(block_mid.split("\n"))
        else:
            lines.extend(chap)
        chars += len(block) + 40
        i += 1
    lines.extend([marker(size, "TAIL"), ""])
    text = "\n".join(lines)
    # 尾部标记未必恰在 98%,补齐长度后置于末端
    if len(text) < size:
        text += "\n" + "补充段落。" * ((size - len(text)) // 6) + "\n" + marker(size, "TAIL2") + "\n"
    out = OUT_DIR / f"longtext-{size//1000}k.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(f"{out.name}: {len(text)} chars, {out.stat().st_size} bytes, 章节 {i}")
    return out


if __name__ == "__main__":
    sizes = [int(x) for x in sys.argv[1:]] or [50000, 1_000_000, 10_000_000, 20_000_000]
    for s in sizes:
        gen(s)
