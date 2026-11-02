#!/usr/bin/env python3
"""适配器实况探针(宿主机执行,设计 §9.2 的运行态子集)。

前置:compose 两个 service 与 model-serving(18351)在位。直接对
127.0.0.1:18353(适配器)与 18351(上游)发请求,验证:

0. config/ov.conf 的 embedding 模型名确实出现在 18351 /v1/models 加载列表
   (llama-server 不校验请求 model 字段,仅靠请求无法发现模型名漂移);
1. 纯文本经适配器与直连上游的向量一致(cosine≈1);
2. OpenViking 裸 parts 多模态形态返回恰好一个 768 维向量;
3. "红色方块"查询下 red 向量相似度高于 blue(视觉语义);
4. 不支持 part 类型被 4xx 拒绝;
5. 适配器 /health 与 /ready 可用。

输出纪律:只输出维度、余弦、状态码等非敏感指标。

用法:python3 tests/e2e_adapter_probe.py(在仓库根目录)
"""
import base64
import http.client
import json
import math
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ADAPTER = "http://127.0.0.1:18353"
UPSTREAM = "http://127.0.0.1:18351"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
results = []


def _configured_model():
    """从 config/ov.conf 读 embedding 模型名;配置不可信时明确终止。

    llama-server 不校验请求 model 字段,回退旧名会在配置损坏时产生假通过。
    """
    try:
        conf = Path(__file__).resolve().parents[1] / "config" / "ov.conf"
        cfg = json.loads(os.path.expandvars(conf.read_text(encoding="utf-8-sig")))
        model = cfg["embedding"]["dense"]["model"]
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise SystemExit(
            f"SETUP-FAIL: 无法读取 embedding.dense.model({type(exc).__name__})"
        ) from None
    if not isinstance(model, str) or not model.strip():
        raise SystemExit("SETUP-FAIL: embedding.dense.model 必须是非空字符串")
    return model


MODEL = _configured_model()


def post(url, payload, timeout=160):
    # timeout 须覆盖适配器排队(30s)+上游超时(120s)+序列化/网络开销
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.status
            raw = resp.read()
        try:
            return status, json.loads(raw)
        except ValueError:
            return status, "invalid-json-response"
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        return exc.code, body[:200]
    except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError) as exc:
        return 0, f"connection-level: {type(exc).__name__}"


def single_vector(body):
    if not isinstance(body, dict) or not isinstance(body.get("data"), list):
        return None
    if len(body["data"]) != 1 or not isinstance(body["data"][0], dict):
        return None
    vector = body["data"][0].get("embedding")
    if not isinstance(vector, list) or not vector:
        return None
    if not all(
        isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)
        for x in vector
    ):
        return None
    if not any(x != 0 for x in vector):
        return None  # 零向量无法归一化,余弦相似度未定义,视为无效响应
    return vector


def embed(url, inp):
    status, body = post(url + "/v1/embeddings", {"model": MODEL, "input": inp})
    if status != 200:
        raise SystemExit(f"embed failed: {url} -> {status} {body}")
    vector = single_vector(body)
    if vector is None:
        raise SystemExit(f"embed failed: {url} -> invalid embedding response schema")
    return vector


def cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    return dot / math.sqrt(sum(x * x for x in a) * sum(y * y for y in b))


def check(name, ok, detail):
    results.append((name, ok, detail))
    print(f"[{name}] {'PASS' if ok else 'FAIL'} {detail}")


def data_url(path):
    with open(path, "rb") as fh:
        return "data:image/png;base64," + base64.b64encode(fh.read()).decode()


def check_loaded_model():
    """配置的模型名必须出现在上游 /v1/models;请求里的 model 字段不足以校验。"""
    try:
        with urllib.request.urlopen(UPSTREAM + "/v1/models", timeout=10) as resp:
            data = json.load(resp)
        items = data.get("data") or data.get("models") or []
        ids = " ".join(
            str(item.get("id") or item.get("name") or "")
            for item in items
            if isinstance(item, dict)
        ).lower()
        check(
            "model-loaded",
            MODEL.lower() in ids,
            f"configured={MODEL} loaded={ids or '(empty)'}",
        )
    except Exception as exc:
        check("model-loaded", False, f"error={type(exc).__name__}")


def main():
    check_loaded_model()

    # 1. 纯文本:适配器与直连上游向量一致性
    q = "红色方块的图片"
    va = embed(ADAPTER, q)
    vu = embed(UPSTREAM, q)
    check("text-parity", len(va) == 768 and len(vu) == 768 and cosine(va, vu) > 0.999,
          f"dim_adapter={len(va)} dim_upstream={len(vu)} cosine={cosine(va, vu):.6f}")

    # 2. OpenViking 裸 parts 多模态形态 → 恰好一个 768 维向量
    red_parts = [
        {"type": "text", "text": "describe this image"},
        {"type": "image_url", "image_url": {"url": data_url(FIXTURES / "red.png")}},
    ]
    status, body = post(ADAPTER + "/v1/embeddings", {"model": MODEL, "input": red_parts})
    vecs = body.get("data", []) if isinstance(body, dict) else []
    v_red = single_vector(body) if status == 200 else None
    check("multimodal-shape", v_red is not None and len(v_red) == 768,
          f"http={status} n_vectors={len(vecs)}")
    # 留存步骤 2 的 red 向量供步骤 3 复用(2b 会覆盖 vecs/status)

    # 2b. 纯图 parts(无 text part)同样包装且可用(2026-10-26 补测)
    img_only = [{"type": "image_url", "image_url": {"url": data_url(FIXTURES / "red.png")}}]
    status, body = post(ADAPTER + "/v1/embeddings", {"model": MODEL, "input": img_only})
    vecs = body.get("data", []) if isinstance(body, dict) else []
    image_only_vector = single_vector(body) if status == 200 else None
    check("image-only-parts", image_only_vector is not None and len(image_only_vector) == 768,
          f"http={status} n_vectors={len(vecs)}")

    # 3. 红蓝语义:red 图向量与查询相似度高于 blue
    # (步骤 2 已对同一 red_parts 得到向量,直接复用,不再重复推理)
    if v_red is None:
        v_red = embed(ADAPTER, red_parts)
    blue_parts = [
        {"type": "text", "text": "describe this image"},
        {"type": "image_url", "image_url": {"url": data_url(FIXTURES / "blue.png")}},
    ]
    v_blue = embed(ADAPTER, blue_parts)
    c_red, c_blue = cosine(v_red, va), cosine(v_blue, va)
    check("red-blue-semantics", c_red > c_blue, f"cosine_red={c_red:.4f} cosine_blue={c_blue:.4f}")

    # 4. 负例:不支持 part 类型 → 4xx
    bad = [{"type": "video_url", "video_url": {"url": "x"}}]
    status, body = post(ADAPTER + "/v1/embeddings", {"model": MODEL, "input": bad})
    check("bad-part-rejected", 400 <= status < 500, f"http={status}")

    # 5. 适配器 health/ready
    for path in ("/health", "/ready"):
        try:
            with urllib.request.urlopen(ADAPTER + path, timeout=5) as resp:
                check(f"adapter{path}", resp.status == 200, f"http={resp.status}")
        except urllib.error.HTTPError as exc:
            check(f"adapter{path}", False, f"http={exc.code}")
        except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError) as exc:
            check(f"adapter{path}", False, f"http=0 error={type(exc).__name__}")

    failed = [r for r in results if not r[1]]
    print(f"SUMMARY: {len(results) - len(failed)}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
