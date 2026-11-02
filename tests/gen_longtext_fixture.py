#!/usr/bin/env python3
"""生成长文本测试夹具(写入 tests/fixtures/longtext/,不入 git,测后删除)。

每个尺寸生成一篇结构化中文长文:多章节、段落内容有变化(避免退化成
重复文本),并在实际字符流头部 ~2%、中部 ~50%、尾部 ~98% 各植入一个唯一
可检索标记(形如 LT{size}-HEAD/MID/TAIL-{hex}),用于验证超长文档深层检索。

文件名使用精确字符数(longtext-<size>.md,2026-11-03 起):按 size//1000
分桶会让同桶不同尺寸静默复用错误夹具(50500 与 50000 同名 longtext-50k)。

用法:python3 tests/gen_longtext_fixture.py [--force] [尺寸字符数 ...]
默认尺寸 50000 1000000 10000000 20000000;已有文件默认跳过,--force 才重建。
"""
import os
import random
import sys
import tempfile
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


def gen(size: int, force: bool = False) -> Path:
    if size < 1000:
        raise ValueError("尺寸至少为 1000 字符,才能布置三个深层检索标记")
    out = OUT_DIR / f"longtext-{size}.md"
    if out.exists() and not force:
        print(f"{out.name}: 已存在,跳过(用 --force 重建)")
        return out

    rng = random.Random(20261026 + size)
    out.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(out.parent), prefix=f".{out.name}.")
    targets = [("HEAD", 0.02), ("MID", 0.50), ("TAIL", 0.98)]
    marker_index = 0
    chars = 0
    chapters = 0
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            def write_block(block: str) -> None:
                nonlocal chars, marker_index
                start = 0
                while marker_index < len(targets):
                    pos, fraction = targets[marker_index]
                    target = int(size * fraction)
                    remaining = len(block) - start
                    if not (chars <= target <= chars + remaining):
                        break
                    offset = start + target - chars
                    segment = block[start:offset]
                    fh.write(segment)
                    chars += len(segment)
                    inserted = "\n" + marker(size, pos) + "\n"
                    fh.write(inserted)
                    chars += len(inserted)
                    start = offset
                    marker_index += 1
                segment = block[start:]
                fh.write(segment)
                chars += len(segment)

            write_block(f"# 长文本测试文档 {size} 字符\n\n")
            while chars < size:
                lines = [f"## 第{chapters}章 {rng.choice(TOPICS)}的{rng.choice(ADJS)}行为", ""]
                for _ in range(rng.randint(3, 6)):
                    lines.append(para(rng, chapters))
                    lines.append("")
                write_block("\n".join(lines) + "\n")
                chapters += 1
        if marker_index != len(targets):
            raise RuntimeError("内部错误:三个检索标记未全部写入")
        os.replace(tmp_name, out)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise

    print(f"{out.name}: {chars} chars, {out.stat().st_size} bytes, 章节 {chapters}")
    return out


if __name__ == "__main__":
    args = sys.argv[1:]
    force = "--force" in args
    try:
        sizes = [int(x) for x in args if x != "--force"] \
            or [50000, 1_000_000, 10_000_000, 20_000_000]
    except ValueError:
        print("用法错误:尺寸必须是整数(可选 --force)", file=sys.stderr)
        sys.exit(2)
    for size in sizes:
        gen(size, force=force)
