"""Small strict JSON-schema vocabulary shared by Codex and local validation."""

from .common import ResearchError


def string(enum=None, minimum=0, maximum=12000):
    result = {"type": "string", "minLength": minimum, "maxLength": maximum}
    if enum:
        result["enum"] = list(enum)
    return result


def array(items, minimum=0, maximum=30):
    return {"type": "array", "items": items, "minItems": minimum, "maxItems": maximum}


def obj(**fields):
    return {"type": "object", "properties": fields, "required": list(fields), "additionalProperties": False}


def validate(value, schema, path="$"):
    kind = schema["type"]
    if kind == "object":
        if not isinstance(value, dict) or set(value) != set(schema["required"]):
            raise ResearchError(f"{path}: 結果の項目が指定形式に一致しません。")
        for key, child in schema["properties"].items():
            validate(value[key], child, f"{path}.{key}")
    elif kind == "array":
        if not isinstance(value, list) or not schema.get("minItems", 0) <= len(value) <= schema.get("maxItems", 1000):
            raise ResearchError(f"{path}: 結果の配列が不正です。")
        for index, item in enumerate(value):
            validate(item, schema["items"], f"{path}[{index}]")
    elif kind == "string":
        if not isinstance(value, str) or not schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", 1000000):
            raise ResearchError(f"{path}: 結果の文字列が不正です。")
        if schema.get("minLength", 0) and not value.strip():
            raise ResearchError(f"{path}: 結果を空白だけにできません。")
    elif kind == "integer":
        if type(value) is not int or not schema.get("minimum", 0) <= value <= schema.get("maximum", 1000000):
            raise ResearchError(f"{path}: 結果の整数が不正です。")
    else:
        raise ResearchError(f"{path}: 未対応の結果形式です。")
    if "enum" in schema and value not in schema["enum"]:
        raise ResearchError(f"{path}: 許可されていない結果の値です。")
    return value
