from __future__ import annotations

import copy
import json
import math
from dataclasses import dataclass
from typing import Any, Literal, Mapping, Sequence

FieldType = Literal["int", "float", "bool", "str", "choice", "str_list", "int_list", "json"]


class ConfigError(ValueError):
    """A research setting is missing, unknown, or has an invalid value."""


@dataclass(frozen=True, slots=True)
class ConfigField:
    name: str
    type: FieldType
    default: Any = None
    required: bool = False
    choices: tuple[str, ...] = ()
    minimum: float | None = None
    maximum: float | None = None
    unique_sorted: bool = False
    label_zh: str = ""
    label_en: str = ""
    help_zh: str = ""
    help_en: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type,
            "default": copy.deepcopy(self.default),
            "required": self.required,
            "choices": list(self.choices),
            "minimum": self.minimum,
            "maximum": self.maximum,
            "unique_sorted": self.unique_sorted,
            "label_zh": self.label_zh,
            "label_en": self.label_en,
            "help_zh": self.help_zh,
            "help_en": self.help_en,
        }


def _is_int(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    return isinstance(value, float) and math.isfinite(value) and value.is_integer()


def _coerce_scalar(field: ConfigField, value: Any, name: str) -> Any:
    kind = field.type
    if kind == "int":
        if not _is_int(value):
            raise ConfigError(f"{name} requires an integer / {name} 必須是整數")
        return int(value)
    if kind == "float":
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            raise ConfigError(f"{name} requires a finite number / {name} 必須是有限數字")
        return float(value)
    if kind == "bool":
        if not isinstance(value, bool):
            raise ConfigError(f"{name} requires true or false / {name} 必須是 true 或 false")
        return value
    if kind == "str":
        if not isinstance(value, str):
            raise ConfigError(f"{name} requires text / {name} 必須是文字")
        return value
    if kind == "choice":
        if not isinstance(value, str) or value not in field.choices:
            raise ConfigError(f"{name} must be one of {list(field.choices)} / {name} 必須是 {list(field.choices)} 其中之一")
        return value
    raise AssertionError(kind)


def _check_range(field: ConfigField, value: Any, name: str) -> None:
    if field.type not in {"int", "float"}:
        return
    if field.minimum is not None and value < field.minimum:
        raise ConfigError(f"{name} must be >= {field.minimum} / {name} 不得小於 {field.minimum}")
    if field.maximum is not None and value > field.maximum:
        raise ConfigError(f"{name} must be <= {field.maximum} / {name} 不得大於 {field.maximum}")


def _normalize_value(field: ConfigField, value: Any) -> Any:
    name = field.name
    if field.type in {"str_list", "int_list"}:
        if not isinstance(value, (list, tuple)):
            raise ConfigError(f"{name} requires a list / {name} 必須是列表")
        item_field = ConfigField(name, "str" if field.type == "str_list" else "int")
        items = [_coerce_scalar(item_field, item, f"{name}[{index}]") for index, item in enumerate(value)]
        if field.unique_sorted:
            items = sorted(set(items))
        return items
    if field.type == "json":
        try:
            json.dumps(value, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"{name} must be JSON-serializable / {name} 必須可轉成 JSON: {exc}") from exc
        return copy.deepcopy(value)
    coerced = _coerce_scalar(field, value, name)
    _check_range(field, coerced, name)
    return coerced


def normalize_config(fields: Sequence[ConfigField], raw: Mapping[str, Any]) -> dict[str, Any]:
    """Validate raw settings and return the resolved, complete settings.

    Every declared field appears in the output (defaults filled in), so a stored
    configuration is always the full, explicit list of assumptions used.
    """

    if not isinstance(raw, Mapping):
        raise ConfigError("Settings must be an object / 設定必須是物件")
    declared = {field.name: field for field in fields}
    unknown = sorted(set(raw) - set(declared))
    if unknown:
        raise ConfigError(f"Unknown settings: {unknown} / 未知的設定: {unknown}")
    resolved: dict[str, Any] = {}
    for field in fields:
        if field.name in raw:
            resolved[field.name] = _normalize_value(field, raw[field.name])
        elif field.required:
            raise ConfigError(f"Missing required setting: {field.name} / 缺少必要設定: {field.name}")
        else:
            default = copy.deepcopy(field.default)
            resolved[field.name] = _normalize_value(field, default) if default is not None else None
    return resolved
