"""scripts/env_file.py 的 Compose env_file 兼容解析与校验测试。"""
import json

import pytest

import env_file as ef


def write(tmp_path, text, name="secrets.env"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_parse_quotes_comments_and_whitespace(tmp_path):
    path = write(
        tmp_path,
        'A="quoted value"  # comment\n'
        "B='single value'\n"
        "C=plain # trailing\n"
        "D=value#literal\n"
        "E = spaced \n",
    )
    env = ef.parse_env_file(path)
    assert env == {
        "A": "quoted value",
        "B": "single value",
        "C": "plain",
        "D": "value#literal",
        "E": "spaced",
    }


def test_parse_indented_export_bom_and_last_wins(tmp_path):
    path = tmp_path / "secrets.env"
    path.write_bytes(
        b"\xef\xbb\xbfZ_AI_API_KEY=first\r\n"
        b"  Z_AI_API_KEY=second\r\n"
        b"export OPENVIKING_ROOT_API_KEY=root\r\n"
    )
    env = ef.parse_env_file(path)
    # Compose 语义:缩进行、export、最后一条生效(与 shell 锚定 grep 不同)。
    assert env["Z_AI_API_KEY"] == "second"
    assert env["OPENVIKING_ROOT_API_KEY"] == "root"


def test_parse_non_utf8_comment_does_not_crash(tmp_path):
    path = tmp_path / "secrets.env"
    path.write_bytes(b"# caf\xe9\nZ_AI_API_KEY=goodkey123\n")
    assert ef.parse_env_file(path)["Z_AI_API_KEY"] == "goodkey123"


def test_precheck_accepts_good_keys_and_requests_generation(tmp_path):
    env = {"Z_AI_API_KEY": "goodkey123"}
    assert ef.precheck_env(env) == "GENERATE"
    env["OPENVIKING_ROOT_API_KEY"] = "rootkey1234567890"
    assert ef.precheck_env(env) == "KEEP"


@pytest.mark.parametrize("zai", ["", "   ", "xxxx", "XxX"])
def test_precheck_rejects_missing_empty_or_placeholder_zai(zai):
    with pytest.raises(ef.EnvFileError):
        ef.precheck_env({"Z_AI_API_KEY": zai})


def test_precheck_rejects_existing_empty_root_key():
    with pytest.raises(ef.EnvFileError):
        ef.precheck_env({"Z_AI_API_KEY": "goodkey123", "OPENVIKING_ROOT_API_KEY": "  "})


@pytest.mark.parametrize("root", ["xxxxx", "changeme", "root", "secret", "short"])
def test_precheck_rejects_placeholder_or_short_root_key(root):
    with pytest.raises(ef.EnvFileError):
        ef.precheck_env({"Z_AI_API_KEY": "goodkey123", "OPENVIKING_ROOT_API_KEY": root})


def _config(zai, root):
    return {
        "vlm": {"api_key": zai},
        "server": {"root_api_key": root},
        "embedding": {"dense": {"model": "embeddinggemma-2"}},
        "rerank": {"model": "qwen3-reranker-0.6b"},
    }


def test_validate_config_expands_and_checks_keys(tmp_path):
    cfg_path = write(
        tmp_path,
        json.dumps(_config("${Z_AI_API_KEY}", "${OPENVIKING_ROOT_API_KEY}")),
        name="ov.conf",
    )
    cfg = ef.validate_config(
        {"Z_AI_API_KEY": "goodkey123", "OPENVIKING_ROOT_API_KEY": "rootkey1234567890"},
        cfg_path,
    )
    assert cfg["vlm"]["api_key"] == "goodkey123"
    assert cfg["server"]["root_api_key"] == "rootkey1234567890"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda c: c.pop("vlm"),
        lambda c: c.pop("server"),
        lambda c: c["vlm"].pop("api_key"),
    ],
)
def test_validate_config_reports_malformed_config_without_traceback(tmp_path, mutate):
    cfg = _config("${Z_AI_API_KEY}", "${OPENVIKING_ROOT_API_KEY}")
    mutate(cfg)
    cfg_path = write(tmp_path, json.dumps(cfg), name="ov.conf")
    with pytest.raises(ef.EnvFileError):
        ef.validate_config(
            {"Z_AI_API_KEY": "goodkey123", "OPENVIKING_ROOT_API_KEY": "rootkey1234567890"},
            cfg_path,
        )


def test_validate_config_rejects_key_mismatch_and_leftover_vars(tmp_path):
    cfg_path = write(
        tmp_path,
        json.dumps(_config("literal-zai", "${OPENVIKING_ROOT_API_KEY}")),
        name="ov.conf",
    )
    with pytest.raises(ef.EnvFileError):
        ef.validate_config(
            {"Z_AI_API_KEY": "different", "OPENVIKING_ROOT_API_KEY": "rootkey1234567890"},
            cfg_path,
        )
    cfg_path = write(
        tmp_path, json.dumps(_config("${UNKNOWN_VAR}", "${OPENVIKING_ROOT_API_KEY}")),
        name="ov.conf",
    )
    with pytest.raises(ef.EnvFileError):
        ef.validate_config(
            {"Z_AI_API_KEY": "goodkey123", "OPENVIKING_ROOT_API_KEY": "rootkey1234567890"},
            cfg_path,
        )
