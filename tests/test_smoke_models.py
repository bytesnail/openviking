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


@pytest.mark.parametrize("answer", ["2", "二", "two", "2。", "答案是2", "答案是二", "the answer is two", "1+1=2", "兩"])
def test_text_probe_accepts_expected_answer(answer):
    failures = []
    sm.smoke_vlm_text(DummyVLM(text=answer), failures)
    assert failures == []


@pytest.mark.parametrize("answer", [
    "1加1等于2", "1 加 1 等于 2。", "1+1 = 2", "1加1等于二", "答案是 1 加 1 等于 2",
])
def test_text_probe_accepts_natural_equation_answers(answer):
    """回显题面的自然正确回答("等于2/等于二"、带空格的 "= 2")不得误判 FAIL
    (2026-11-07 检视:旧判据只认 "=2" 结尾,健康 VLM 会被 smoke 误报故障)。"""
    failures = []
    sm.smoke_vlm_text(DummyVLM(text=answer), failures)
    assert failures == []


@pytest.mark.parametrize("answer", ["1加1等于二十", "1加1等于2.5", "不等于2"])
def test_text_probe_rejects_equation_lookalikes(answer):
    """等式判据的前瞻排除:"等于二十/等于2.5" 是其他数,"不等于2" 是否定式。"""
    failures = []
    sm.smoke_vlm_text(DummyVLM(text=answer), failures)
    assert failures == ["vlm-text"]


@pytest.mark.parametrize("answer", ["1加1=2万", "1+1=2两"])
def test_text_probe_rejects_ascii_equation_cn_suffix(answer):
    """'=2' 分支的前瞻与中文分支对称排除中文数字:"2万/2两" 是其他数,
    不得因子串含 "2" 放行(2026-11-07 sweep)。"""
    failures = []
    sm.smoke_vlm_text(DummyVLM(text=answer), failures)
    assert failures == ["vlm-text"]


@pytest.mark.parametrize("answer", ["十二", "二十", "二零二五", "二点五", "三百二十"])
def test_text_probe_rejects_wrong_chinese_numerals(answer):
    """含"二/兩/两"的其他数字(12/20/2025/2.5/320)不得因子串含"二"被
    误判通过;_MULTI_DIGIT 只匹配 ASCII \\d,管不住中文数字
    (2026-11-03 检视:旧版子串兜底对"十二"打印 OK)。"""
    failures = []
    sm.smoke_vlm_text(DummyVLM(text=answer), failures)
    assert failures == ["vlm-text"]


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


def test_image_probe_rejects_hyphenated_negation(monkeypatch):
    """"non-red" 不含可被 \\bno\\b 命中的否定词,却含 \\bred\\b 的子串:
    否定防线必须覆盖连字符形态(2026-11-07 sweep)。"""
    monkeypatch.setattr(
        sm, "FIXTURES", str(Path(__file__).resolve().parent / "fixtures")
    )
    failures = []
    sm.smoke_vlm_image(DummyVLM(image="The image is non-red"), failures)
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


# ---------- smoke_rerank 语义判定路径(以 sys.modules 注入假 rerank client) ----------


def run_rerank_probe(monkeypatch, scores):
    """注入假 openviking rerank client(宿主无 site-packages,延迟导入在
    函数内),返回 failures 列表;覆盖 ordered/unordered/invalid 三分支
    (2026-11-03 检视:该路径原先零测试覆盖)。"""
    import sys
    import types

    class FakeClient:
        @classmethod
        def from_config(cls, cfg):
            client = cls()
            client.rerank_batch = lambda query, docs: scores
            return client

    fake_module = types.ModuleType("openviking.models.rerank.openai_rerank")
    fake_module.OpenAIRerankClient = FakeClient
    for name in ("openviking", "openviking.models", "openviking.models.rerank"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    monkeypatch.setitem(
        sys.modules, "openviking.models.rerank.openai_rerank", fake_module
    )
    failures = []
    sm.smoke_rerank({"model": "m"}, failures)
    return failures


def test_rerank_probe_passes_when_relevant_scores_higher(monkeypatch, capsys):
    assert run_rerank_probe(monkeypatch, [0.9, 0.2]) == []
    assert "[rerank]     OK" in capsys.readouterr().out


def test_rerank_probe_fails_when_irrelevant_scores_higher(monkeypatch, capsys):
    """相关文档得分反而更低:必须 FAIL 并计入 failures,不得为倒置的
    rerank 打印 OK。"""
    assert run_rerank_probe(monkeypatch, [0.2, 0.9]) == ["rerank"]
    assert "[rerank]     FAIL" in capsys.readouterr().out


def test_rerank_probe_reports_invalid_scores(monkeypatch, capsys):
    assert run_rerank_probe(monkeypatch, ["0.9", 0.2]) == ["rerank"]
    assert "invalid_scores" in capsys.readouterr().out
