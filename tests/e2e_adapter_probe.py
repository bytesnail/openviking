#!/usr/bin/env python3
"""适配器实况探针(宿主机执行,设计 §9.2 的运行态子集)。

前置:compose 两个 service 与 model-serving(18351)在位。直接对
127.0.0.1:18353(适配器)与 18351(上游)发请求,验证:

1. 纯文本经适配器与直连上游的向量一致(cosine≈1);
2. OpenViking 裸 parts 多模态形态返回恰好一个 768 维向量;
3. "红色方块"查询下 red 向量相似度高于 blue(视觉语义);
4. 不支持 part 类型被 4xx 拒绝;
5. 适配器 /health 与 /ready 可用。

输出纪律:只输出维度、余弦、状态码等非敏感指标。

用法:python3 tests/e2e_adapter_probe.py(在仓库根目录)
"""
import base64
import json
import math
import sys
import urllib.error
import urllib.request
from pathlib import Path

ADAPTER = "http://127.0.0.1:18353"
UPSTREAM = "http://127.0.0.1:18351"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
results = []


def post(url, payload, timeout=60):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        return exc.code, body[:200]


def embed(url, inp):
    status, body = post(url + "/v1/embeddings", {"model": "embeddinggemma-2", "input": inp})
    if status != 200:
        raise SystemExit(f"embed failed: {url} -> {status} {body}")
    return body["data"][0]["embedding"]


def cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    return dot / math.sqrt(sum(x * x for x in a) * sum(y * y for y in b))


def check(name, ok, detail):
    results.append((name, ok, detail))
    print(f"[{name}] {'PASS' if ok else 'FAIL'} {detail}")


def data_url(path):
    with open(path, "rb") as fh:
        return "data:image/png;base64," + base64.b64encode(fh.read()).decode()


def main():
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
    status, body = post(ADAPTER + "/v1/embeddings", {"model": "embeddinggemma-2", "input": red_parts})
    vecs = body.get("data", []) if isinstance(body, dict) else []
    check("multimodal-shape", status == 200 and len(vecs) == 1 and len(vecs[0]["embedding"]) == 768,
          f"http={status} n_vectors={len(vecs)}")

    # 3. 红蓝语义:red 图向量与查询相似度高于 blue
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
    status, body = post(ADAPTER + "/v1/embeddings", {"model": "embeddinggemma-2", "input": bad})
    check("bad-part-rejected", 400 <= status < 500, f"http={status}")

    # 5. 适配器 health/ready
    for path in ("/health", "/ready"):
        try:
            with urllib.request.urlopen(ADAPTER + path, timeout=5) as resp:
                check(f"adapter{path}", resp.status == 200, f"http={resp.status}")
        except urllib.error.HTTPError as exc:
            check(f"adapter{path}", False, f"http={exc.code}")

    failed = [r for r in results if not r[1]]
    print(f"SUMMARY: {len(results) - len(failed)}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
