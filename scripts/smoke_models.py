#!/usr/bin/env python
"""模型链路冒烟(设计 §9.3):GLM 文本/图像 Chat Completions + 真实 rerank。

在 openviking 容器内执行:
    python -I -B /opt/openviking/scripts/smoke_models.py

输出纪律:只输出状态与非敏感指标(字符数、得分);不输出密钥、完整模型
响应或异常文本(只打印异常类型名)。VLM 调用消耗少量 Coding Plan 用量。
"""
import json
import os
import sys

FIXTURES = "/opt/openviking/fixtures"


def load_config():
    path = os.environ.get("OPENVIKING_CONFIG_FILE", "/app/.openviking/ov.conf")
    raw = os.path.expandvars(open(path, encoding="utf-8-sig").read())
    return json.loads(raw)


def smoke_vlm_text(vlm, failures):
    try:
        text = vlm.get_completion(prompt="用一个词回答:1 加 1 等于几?")
        ok = isinstance(text, str) and bool(text.strip())
        hint = ("2" in text or "二" in text or "兩" in text or "两" in text) if ok else False
        print(f"[vlm-text]   {'OK' if ok else 'FAIL'} chars={len(text) if isinstance(text, str) else 0} answer_correct={hint}")
        if not ok:
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
        ok = isinstance(text, str) and bool(text.strip())
        mentions_red = ("红" in text or "red" in text.lower()) if ok else False
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
    from openviking.models.rerank.openai_rerank import OpenAIRerankClient

    client = OpenAIRerankClient.from_config(rerank_cfg)
    if client is None:
        print("[rerank]     FAIL error=client_unavailable")
        failures.append("rerank")
        return
    try:
        scores = client.rerank_batch(
            "红色方块的图片",
            ["一张纯红色方块的图片", "今天的午餐菜单是意大利面"],
        )
        if not scores or len(scores) != 2:
            print(f"[rerank]     FAIL scores={scores!r}")
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
    from openviking_cli.utils.config.open_viking_config import OpenVikingConfig

    cfg = OpenVikingConfig.from_dict(load_config())
    failures = []
    print("smoke_models: 开始(会发起 2 次 GLM 真实调用与 1 次 rerank 真实请求)")
    smoke_vlm_text(cfg.vlm, failures)
    smoke_vlm_image(cfg.vlm, failures)
    smoke_rerank(cfg.rerank, failures)
    if failures:
        print(f"smoke_models: FAIL({', '.join(failures)})")
        return 1
    print("smoke_models: 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
