from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel


def _normalize_payload(payload: BaseModel | dict[str, Any] | list[Any]) -> Any:
    if isinstance(payload, BaseModel):
        return payload.model_dump(mode="json")
    if isinstance(payload, list):
        return [
            item.model_dump(mode="json") if isinstance(item, BaseModel) else item
            for item in payload
        ]
    return payload


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_json(path: Path, payload: BaseModel | dict[str, Any] | list[Any]) -> None:
    ensure_dir(path.parent)
    content = _normalize_payload(payload)
    path.write_text(json.dumps(content, indent=2, sort_keys=True), encoding="utf-8")


def write_yaml(path: Path, payload: BaseModel | dict[str, Any] | list[Any]) -> None:
    ensure_dir(path.parent)
    content = _normalize_payload(payload)
    path.write_text(yaml.safe_dump(content, sort_keys=False), encoding="utf-8")
