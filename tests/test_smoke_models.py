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


@pytest.mark.parametrize("answer", ["2", "二", "two", "2。", "答案是2", "1+1=2"])
def test_text_probe_accepts_expected_answer(answer):
    failures = []
    sm.smoke_vlm_text(DummyVLM(text=answer), failures)
    assert failures == []


@pytest.mark.parametrize("answer", [
    "blue", "The image is not red; it is blue.", "hundred", "不是红色",
])
def test_image_probe_rejects_nonempty_wrong_color(monkeypatch, answer):
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
