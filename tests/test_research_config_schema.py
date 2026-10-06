import pytest

from treepolo_mlb_data.research.config_schema import ConfigError, ConfigField, normalize_config

FIELDS = (
    ConfigField("n", "int", default=3, minimum=1, maximum=10),
    ConfigField("ratio", "float", default=0.5, minimum=0.0, maximum=1.0),
    ConfigField("flag", "bool", default=False),
    ConfigField("label", "str", default="x"),
    ConfigField("mode", "choice", default="a", choices=("a", "b")),
    ConfigField("names", "str_list", default=["z"], unique_sorted=True),
    ConfigField("years", "int_list", default=[2024], unique_sorted=True),
    ConfigField("order", "str_list", default=["b", "a"]),
    ConfigField("blob", "json", default={"k": [1]}),
    ConfigField("must", "int", required=True),
)


def test_defaults_are_filled_and_every_field_is_present():
    out = normalize_config(FIELDS, {"must": 1})
    assert set(out) == {field.name for field in FIELDS}
    assert out["n"] == 3 and out["ratio"] == 0.5 and out["flag"] is False
    assert out["names"] == ["z"] and out["order"] == ["b", "a"] and out["blob"] == {"k": [1]}


def test_defaults_are_copied_not_shared():
    first = normalize_config(FIELDS, {"must": 1})
    first["blob"]["k"].append(2)
    second = normalize_config(FIELDS, {"must": 1})
    assert second["blob"] == {"k": [1]}


def test_unknown_keys_are_rejected_not_ignored():
    with pytest.raises(ConfigError, match="Unknown settings"):
        normalize_config(FIELDS, {"must": 1, "typo": 1, "other": 2})


def test_missing_required_and_non_dict():
    with pytest.raises(ConfigError, match="Missing required"):
        normalize_config(FIELDS, {})
    with pytest.raises(ConfigError):
        normalize_config(FIELDS, [1, 2])  # type: ignore[arg-type]


def test_int_rules():
    assert normalize_config(FIELDS, {"must": 2.0})["must"] == 2
    for bad in (True, "3", 2.5, float("nan")):
        with pytest.raises(ConfigError):
            normalize_config(FIELDS, {"must": bad})
    with pytest.raises(ConfigError, match="<="):
        normalize_config(FIELDS, {"must": 1, "n": 11})
    with pytest.raises(ConfigError, match=">="):
        normalize_config(FIELDS, {"must": 1, "n": 0})


def test_float_rules():
    assert normalize_config(FIELDS, {"must": 1, "ratio": 1})["ratio"] == 1.0
    for bad in (True, "0.1", float("inf"), float("nan")):
        with pytest.raises(ConfigError):
            normalize_config(FIELDS, {"must": 1, "ratio": bad})
    with pytest.raises(ConfigError):
        normalize_config(FIELDS, {"must": 1, "ratio": 1.5})


def test_bool_str_choice_rules():
    with pytest.raises(ConfigError):
        normalize_config(FIELDS, {"must": 1, "flag": 1})
    with pytest.raises(ConfigError):
        normalize_config(FIELDS, {"must": 1, "label": 5})
    with pytest.raises(ConfigError, match="must be one of"):
        normalize_config(FIELDS, {"must": 1, "mode": "c"})
    assert normalize_config(FIELDS, {"must": 1, "mode": "b"})["mode"] == "b"


def test_lists_sorting_and_types():
    out = normalize_config(FIELDS, {"must": 1, "names": ("b", "a", "b"), "years": [2024, 2023, 2024], "order": ["b", "a", "b"]})
    assert out["names"] == ["a", "b"]
    assert out["years"] == [2023, 2024]
    assert out["order"] == ["b", "a", "b"]  # order is meaningful unless unique_sorted
    with pytest.raises(ConfigError):
        normalize_config(FIELDS, {"must": 1, "years": ["2024"]})
    with pytest.raises(ConfigError):
        normalize_config(FIELDS, {"must": 1, "names": "a"})


def test_json_field_must_be_serializable():
    assert normalize_config(FIELDS, {"must": 1, "blob": {"a": [1, 2]}})["blob"] == {"a": [1, 2]}
    with pytest.raises(ConfigError):
        normalize_config(FIELDS, {"must": 1, "blob": {"a": float("nan")}})
    with pytest.raises(ConfigError):
        normalize_config(FIELDS, {"must": 1, "blob": {"a": object()}})


def test_error_messages_are_bilingual_and_name_the_field():
    with pytest.raises(ConfigError) as info:
        normalize_config(FIELDS, {"must": "x"})
    message = str(info.value)
    assert "must" in message and "必須" in message


def test_field_to_dict_is_json_friendly():
    import json
    for field in FIELDS:
        json.dumps(field.to_dict())
