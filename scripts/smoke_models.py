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
_IMAGE_NEGATION = re.compile(_NEGATION.pattern + r"|不红|不紅|非红|非紅|无红|无紅")
_MULTI_DIGIT = re.compile(r"\d{2,}")
_STANDALONE_TWO = re.compile(r"(?<!\d)2(?!\d)")
_ENGLISH_TWO = re.compile(r"(?<![a-z-])two(?![a-z-])")
_RED_WORD = re.compile(r"\bred\b")


def _normalize_answer(text):
    if not isinstance(text, str):
        return ""
    return text.strip().lower().rstrip("。.!！?？ ")


def _text_answer_ok(text):
    """接受 correct 的 2/二/two 及自然短语;拒绝 2025/12/否定句。"""
    normalized = _normalize_answer(text)
    if not normalized:
        return False
    if normalized in {"2", "二", "兩", "两", "two"}:
        return True
    if _NEGATION.search(normalized) or _MULTI_DIGIT.search(normalized):
        return False
    # 允许 "答案是2/二/two" 与 "1+1=2" 等自然回答,但排除多位数和否定句。
    return (
        _STANDALONE_TWO.search(normalized) is not None
        or _ENGLISH_TWO.search(normalized) is not None
        or any(token in normalized for token in ("二", "兩", "两"))
    )


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
        # 但允许 "答案是2"/"1+1=2" 这类自然正确回答。
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
        ok = mentions_red
        print(
            f"[vlm-image]  {'OK' if ok else 'FAIL'} chars={len(text) if isinstance(text, str) else 0} "
            f"mentions_red={mentions_red}"
        )
        if not ok:
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
    # VLM client 的 lazy init 非线程安全,先在主线程完成;三个探针互不依赖,
    # 有界并发只缩短墙钟,不增加请求数/token。各自使用独立失败列表保序汇总。
    try:
        cfg.vlm.get_vlm_instance()
    except Exception:
        pass  # 让各探针沿用原有错误分类与输出纪律

    def run_probe(probe, arg):
        local_failures = []
        probe(arg, local_failures)
        return local_failures

    probes = [
        (smoke_vlm_text, cfg.vlm),
        (smoke_vlm_image, cfg.vlm),
        (smoke_rerank, cfg.rerank),
    ]
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(run_probe, probe, arg) for probe, arg in probes]
        for future in futures:
            failures.extend(future.result())
    if failures:
        print(f"smoke_models: FAIL({', '.join(failures)})")
        return 1
    print("smoke_models: 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
