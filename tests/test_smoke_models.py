"""smoke_models 语义判据:非空但错误的模型响应不得判成功。"""
from pathlib import Path

import smoke_models as sm


class DummyVLM:
    def __init__(self, text="", image=""):
        self.text = text
        self.image = image

    def get_completion(self, prompt):
        return self.text

    def get_vision_completion(self, prompt, images):
        return self.image


def test_text_probe_rejects_nonempty_wrong_answer():
    failures = []
    sm.smoke_vlm_text(DummyVLM(text="banana"), failures)
    assert failures == ["vlm-text"]


def test_text_probe_accepts_expected_answer():
    failures = []
    sm.smoke_vlm_text(DummyVLM(text="two"), failures)
    assert failures == []


def test_image_probe_rejects_nonempty_wrong_color(monkeypatch):
    monkeypatch.setattr(
        sm, "FIXTURES", str(Path(__file__).resolve().parent / "fixtures")
    )
    failures = []
    sm.smoke_vlm_image(DummyVLM(image="blue"), failures)
    assert failures == ["vlm-image"]


def test_image_probe_accepts_red(monkeypatch):
    monkeypatch.setattr(
        sm, "FIXTURES", str(Path(__file__).resolve().parent / "fixtures")
    )
    failures = []
    sm.smoke_vlm_image(DummyVLM(image="red"), failures)
    assert failures == []
