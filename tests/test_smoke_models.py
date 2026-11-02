"""smoke_models 语义判据:非空但错误的模型响应不得判成功。"""
from pathlib import Path

import pytest

import smoke_models as sm


class DummyVLM:
    def __init__(self, text="", image=""):
        self.text = text
        self.image = image

    def get_completion(self, prompt):
        return self.text

    def get_vision_completion(self, prompt, images):
        return self.image


@pytest.mark.parametrize("answer", [
    "banana", "2025", "12", "I cannot compute 2+2", "不知道,2?",
])
def test_text_probe_rejects_nonempty_wrong_answer(answer):
    failures = []
    sm.smoke_vlm_text(DummyVLM(text=answer), failures)
    assert failures == ["vlm-text"]


@pytest.mark.parametrize("answer", ["1+1不等于2", "答案不是2", "没有2", "不等于二"])
def test_text_probe_rejects_chinese_negation_forms(answer):
    """中文否定式(没有/不等于/不是)不得因含 "2"/"二" 被误判通过(2026-11-02 检视)。"""
    failures = []
    sm.smoke_vlm_text(DummyVLM(text=answer), failures)
    assert failures == ["vlm-text"]


@pytest.mark.parametrize("answer", ["2", "二", "two", "2。", "答案是2", "答案是二", "the answer is two", "1+1=2"])
def test_text_probe_accepts_expected_answer(answer):
    failures = []
    sm.smoke_vlm_text(DummyVLM(text=answer), failures)
    assert failures == []


@pytest.mark.parametrize("answer", [
    "blue", "The image is not red; it is blue.", "The image isn't red", "不红", "hundred", "不是红色",
])
def test_image_probe_rejects_nonempty_wrong_color(monkeypatch, answer):
    monkeypatch.setattr(
        sm, "FIXTURES", str(Path(__file__).resolve().parent / "fixtures")
    )
    failures = []
    sm.smoke_vlm_image(DummyVLM(image=answer), failures)
    assert failures == ["vlm-image"]


@pytest.mark.parametrize("answer", [
    "图中没有红色", "图中无红色", "图中不含红色", "图中不存在红色",
])
def test_image_probe_rejects_chinese_negation_forms(monkeypatch, answer):
    """中文否定式(没有/无红/不含/不存在)含 "红" 字时不得误判通过(2026-11-02 检视)。"""
    monkeypatch.setattr(
        sm, "FIXTURES", str(Path(__file__).resolve().parent / "fixtures")
    )
    failures = []
    sm.smoke_vlm_image(DummyVLM(image=answer), failures)
    assert failures == ["vlm-image"]


@pytest.mark.parametrize("answer", ["red", "红", "红色", "red。", "The image is red", "这张图片是红色"])
def test_image_probe_accepts_red(monkeypatch, answer):
    monkeypatch.setattr(
        sm, "FIXTURES", str(Path(__file__).resolve().parent / "fixtures")
    )
    failures = []
    sm.smoke_vlm_image(DummyVLM(image=answer), failures)
    assert failures == []


@pytest.mark.parametrize("scores", [
    [True, False], [float("inf"), 0.0], [float("nan"), 0.0],
    [0.8], [0.8, 0.2, 0.1], ["0.8", 0.2], None,
])
def test_rerank_rejects_malformed_scores(scores):
    assert not sm._valid_rerank_scores(scores)


def test_rerank_accepts_finite_numeric_scores():
    assert sm._valid_rerank_scores([0.8, 0.2])
