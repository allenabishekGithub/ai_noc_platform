from __future__ import annotations

import ast
import json
import re
from typing import Any


def extract_json_object(text: str) -> dict[str, Any]:
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < 0 or end <= start:
        raise ValueError("No JSON object found in model output")
    return json.loads(text[start : end + 1])


def safe_eval(expression: str) -> float:
    allowed = (
        ast.Expression,
        ast.BinOp,
        ast.UnaryOp,
        ast.Add,
        ast.Sub,
        ast.Mult,
        ast.Div,
        ast.Mod,
        ast.Pow,
        ast.USub,
        ast.UAdd,
        ast.Constant,
        ast.Load,
        ast.FloorDiv,
    )
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ValueError("Unsupported expression") from exc
    for node in ast.walk(tree):
        if not isinstance(node, allowed):
            raise ValueError("Unsupported expression")
    return eval(compile(tree, "<calc>", "eval"), {"__builtins__": {}}, {})


def normalize_agent_name(name: str, fallback: str = "Specialist") -> str:
    text = str(name or "").strip()
    if not text:
        return fallback
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    text = re.sub(r"[_-]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    tokens = [token for token in text.split(" ") if token.lower() not in {"agent", "assistant"}]
    cleaned = " ".join(tokens).strip()
    if not cleaned:
        cleaned = fallback
    return cleaned.title()
