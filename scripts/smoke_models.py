#!/usr/bin/env python
"""模型链路冒烟(设计 §9.3):GLM 文本/图像 Chat Completions + 真实 rerank。

在 openviking 容器内执行:
    python -I -B /opt/openviking/scripts/smoke_models.py

输出纪律:只输出状态与非敏感指标(字符数、得分);不输出密钥、完整模型
响应或异常文本(只打印异常类型名)。VLM 调用消耗少量 Coding Plan 用量。
"""
import concurrent.futures
import math
import os
import re
import sys
from pathlib import Path

FIXTURES = "/opt/openviking/fixtures"

_NEGATION = re.compile(
    r"\b(?:not|no|never|cannot|can't|isn't|aren't|wasn't|weren't)\b"
    r"|不是|并非|不知道|无法|没有|不含|不等于|不存在"
)
# non-?red:\bno\b 匹配不到 "non-red"(no 后接词字符无边界),而 \bred\b
# 恰好命中其子串 red,否定式错误回答会因子串放行(2026-11-07 sweep)。
_IMAGE_NEGATION = re.compile(
    _NEGATION.pattern + r"|不红|不紅|非红|非紅|无红|无紅|non-?red\b"
)
_MULTI_DIGIT = re.compile(r"\d{2,}")
# 独立 ASCII 数字:用于拒绝含"非 2 的其他数字"的回答("2+2=4"/"2 或 3"
# 都是对"1+1=?"的错误应答,不能因子串含 "2" 放行)。
_STANDALONE_DIGIT = re.compile(r"(?<!\d)(\d)(?!\d)")
# 中文数字语境字符集:数字复合词判定与"其他独立中文数字"拒绝(决策 C)
# 共用,语境语义只此一份。
_CN_NUMERAL_CONTEXT = "零〇○一二兩两三四五六七八九十百千万亿点0-9"
# 其他独立中文数字:不含一(题面"一加一"回显)与二/兩/两(答案本身);
# 出现即是对"1 加 1"的错误应答("二或三"),与 ASCII 分支的
# _STANDALONE_DIGIT+all 判定对称(2026-11-08 决策 C)。
_OTHER_STANDALONE_CN_NUMERAL = re.compile(
    f"(?<![{_CN_NUMERAL_CONTEXT}])[三四五六七八九十百千万亿零〇]"
    f"(?![{_CN_NUMERAL_CONTEXT}])"
)
# 答案词(精确匹配与模板剥除后共用):与旧精确集合一致(含简繁"兩/两"),
# 收紧后模板剥除分支与精确分支共用同一集合,语义只此一份。
_ANSWER_WORDS = {"2", "二", "兩", "two", "两"}
# 答案前缀模板(决策 B):剥除后剩余必须恰为答案词。模板是有限的答案
# 框定词而非开放词表——多了会把借题发挥放行,少了会误报健康 VLM,
# 调整须同步 tests/test_smoke_models.py 的 accepts/rejects 钉面。
_ANSWER_PREFIX = re.compile(
    r"^(?:the\s+answer\s+is|answer|答案|结果|(?:当然|应该|可能|一定|那|这)?就?是)"
    r"[\s:=：为是]*"
)
_RED_WORD = re.compile(r"\bred\b")
# 自然等式形态:"1+1 = 2"("= 2" 带空格)与"1加1等于2/等于二"都不以 "=2"
# 结尾,却是对题面的自然正确回答(2026-11-07 检视:旧判据只认 "=2" 结尾,
# 会把回显题面的正确回答误判 FAIL,smoke 对健康 VLM 误报故障)。两个分支
# 的前瞻对称排除以 2 起头的其他数:"等于二十/2.5"与"1+1=2万/2两"
# (2026-11-07 sweep 补齐 ASCII 分支的中文数字排除)。
_EQUATION_TWO = re.compile(
    r"=\s*2(?![\d.．二兩两三四五六七八九十百千万亿零〇点])"
    r"|等[于於]\s*[二兩两2](?![点零〇\d.．二兩两三四五六七八九十百千万亿])"
)


def _normalize_answer(text):
    if not isinstance(text, str):
        return ""
    return text.strip().lower().rstrip("。.!！?？ ")


def _text_answer_ok(text):
    """接受 correct 的 2/二/两/two 的本质答案形态;拒绝 2025/12/否定句、
    "二X"构词借题发挥(二氧化硫/二进制)与其他独立数字("二或三")。

    形状白名单(2026-11-08 用户决策 B+C,取代旧"子串证据 + all([])
    空真终检"——旧路径对"二氧化硫是一种气体"类"二X"构词与"二或三"
    类混合数字假通过,实证 5 例):① 精确答案词;② 等式形态("=2" 结尾或"等于二"自然等式,
    前瞻排除"二十/2.5"类尾随数);③ 答案前缀模板剥除后恰为答案词
    ("答案是二"/"the answer is two"/"这是二")。非等式形态下,ASCII 或
    中文的其他独立数字一律拒绝("2 或 3"/"二或三" 对称防线)。"""
    normalized = _normalize_answer(text)
    if not normalized:
        return False
    if normalized in _ANSWER_WORDS:
        return True
    if _NEGATION.search(normalized) or _MULTI_DIGIT.search(normalized):
        return False
    if normalized.endswith("=2") or _EQUATION_TWO.search(normalized) is not None:
        return True
    # 非等式形态:其他独立数字(ASCII/中文)即拒;剩余只认前缀模板形状
    digits = _STANDALONE_DIGIT.findall(normalized)
    if not all(d == "2" for d in digits):
        return False
    if _OTHER_STANDALONE_CN_NUMERAL.search(normalized):
        return False
    return _ANSWER_PREFIX.sub("", normalized, count=1) in _ANSWER_WORDS


def _image_answer_ok(text):
    """接受 red/红/红色 及自然短语;拒绝否定句、blue/hundred。"""
    normalized = _normalize_answer(text)
    if not normalized:
        return False
    if normalized in {"红", "紅", "红色", "紅色", "red"}:
        return True
    if (
        _IMAGE_NEGATION.search(normalized)
        or "blue" in normalized
        or "蓝" in normalized
        or "藍" in normalized
    ):
        return False
    return _RED_WORD.search(normalized) is not None or "红" in normalized or "紅" in normalized


def _valid_rerank_scores(scores):
    if not (isinstance(scores, (list, tuple)) and len(scores) == 2):
        return False
    # 同型"有限数值"判定在 scripts/embedding_adapter.py(single_vector)与
    # scripts/bootstrap_auth.py(_clamp_cfg_timeout,经 _ensure_timeout_file
    # 作用于磁盘文件)各有一份(-I 隔离运行);
    # 修改溢出/布尔排除等语义须三处同步。
    try:
        return all(
            isinstance(score, (int, float))
            and not isinstance(score, bool)
            and math.isfinite(score)
            for score in scores
        )
    except OverflowError:
        # JSON 可表达任意精度整数,超出 float 范围按无效分数处理而非崩溃。
        return False


def load_config():
    path = Path(os.environ.get("OPENVIKING_CONFIG_FILE", "/app/.openviking/ov.conf"))
    # 与 CLI 共用官方解析/展开逻辑;延迟导入保留宿主纯函数单测可收集性。
    from openviking_cli.utils.config.config_loader import load_json_config
    return load_json_config(path)


def smoke_vlm_text(vlm, failures):
    try:
        text = vlm.get_completion(prompt="用一个词回答:1 加 1 等于几?")
        # 语义判据:不接受 "2025"/"12"/否定句等含 "2" 的 substring 假通过,
        # 但允许 "答案是2"/"1+1=2" 这类自然正确回答;"二X"构词(二氧化硫/
        # 二进制)与其他独立数字("二或三")同样拒绝(2026-11-08 B+C)。
        hint = _text_answer_ok(text)
        print(f"[vlm-text]   {'OK' if hint else 'FAIL'} chars={len(text) if isinstance(text, str) else 0} answer_correct={hint}")
        if not hint:
            failures.append("vlm-text")
    except Exception as exc:
        print(f"[vlm-text]   FAIL error={type(exc).__name__}")
        failures.append("vlm-text")


def smoke_vlm_image(vlm, failures):
    try:
        # 注意:v0.5.2 _prepare_image 对非 http(s) 字符串一律按文件路径处理,
        # data-URL 字符串会 FileNotFoundError;必须传原始 bytes
        with open(f"{FIXTURES}/red.png", "rb") as fh:
            image_bytes = fh.read()
        text = vlm.get_vision_completion(
            prompt="这张图片的主要颜色是什么?只用一个词回答。", images=[image_bytes]
        )
        # 语义判据:拒绝 "not red"/"hundred"/blue 等 substring/否定假通过,
        # 但允许 "这张图片是红色" 这类自然正确回答。
        mentions_red = _image_answer_ok(text)
        print(
            f"[vlm-image]  {'OK' if mentions_red else 'FAIL'} chars={len(text) if isinstance(text, str) else 0} "
            f"mentions_red={mentions_red}"
        )
        if not mentions_red:
            failures.append("vlm-image")
    except Exception as exc:
        print(f"[vlm-image]  FAIL error={type(exc).__name__}")
        failures.append("vlm-image")


def smoke_rerank(rerank_cfg, failures):
    try:
        from openviking.models.rerank.openai_rerank import OpenAIRerankClient
        client = OpenAIRerankClient.from_config(rerank_cfg)
    except Exception as exc:
        print(f"[rerank]     FAIL error={type(exc).__name__}")
        failures.append("rerank")
        return
    if client is None:
        print("[rerank]     FAIL error=client_unavailable")
        failures.append("rerank")
        return
    try:
        scores = client.rerank_batch(
            "红色方块的图片",
            ["一张纯红色方块的图片", "今天的午餐菜单是意大利面"],
        )
        if not _valid_rerank_scores(scores):
            print("[rerank]     FAIL reason=invalid_scores")
            failures.append("rerank")
            return
        ordered = scores[0] > scores[1]
        print(
            f"[rerank]     {'OK' if ordered else 'FAIL'} "
            f"relevant={scores[0]:.3f} irrelevant={scores[1]:.3f}"
        )
        if not ordered:
            failures.append("rerank")
    except Exception as exc:
        print(f"[rerank]     FAIL error={type(exc).__name__}")
        failures.append("rerank")


def main():
    # 输出纪律:配置加载/解析失败同样只报异常类型,不吐原始 traceback
    try:
        from openviking_cli.utils.config.open_viking_config import OpenVikingConfig
        cfg = OpenVikingConfig.from_dict(load_config())
    except Exception as exc:
        print(f"smoke_models: FAIL(config error={type(exc).__name__})")
        return 1
    failures = []
    print("smoke_models: 开始(会发起 2 次 GLM 真实调用与 1 次 rerank 真实请求)")
    # VLM client 的 lazy init 非线程安全,先在主线程完成;预热失败时改为
    # 串行执行全部探针,避免两个 VLM 探针并发撞上未初始化的共享 client——
    # 静默吞掉预热失败只会把这道防线变成竞态本身(2026-11-03 检视)。
    # 各探针沿用原有错误分类与输出纪律;串行只发生在预热失败的退化路径。
    try:
        cfg.vlm.get_vlm_instance()
        vlm_ready = True
    except Exception:
        vlm_ready = False

    def run_probe(probe, arg):
        local_failures = []
        probe(arg, local_failures)
        return local_failures

    probes = [
        (smoke_vlm_text, cfg.vlm),
        (smoke_vlm_image, cfg.vlm),
        (smoke_rerank, cfg.rerank),
    ]
    if vlm_ready:
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            futures = [executor.submit(run_probe, probe, arg) for probe, arg in probes]
            for future in futures:
                failures.extend(future.result())
    else:
        for probe, arg in probes:
            failures.extend(run_probe(probe, arg))
    if failures:
        print(f"smoke_models: FAIL({', '.join(failures)})")
        return 1
    print("smoke_models: 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
