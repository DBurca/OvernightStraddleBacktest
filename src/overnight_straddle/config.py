from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class Config:
    raw: dict[str, Any]

    @property
    def tickers(self) -> list[str]:
        t = self.raw.get("tickers", [])
        if not isinstance(t, list) or not all(isinstance(x, str) for x in t):
            raise ValueError("config: tickers must be a list of strings")
        return [x.strip().upper() for x in t if x.strip()]

    def get(self, path: str, default: Any = None) -> Any:
        cur: Any = self.raw
        for part in path.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur


def load_config(path: str | Path) -> Config:
    p = Path(path)
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("config root must be a mapping")
    return Config(raw=raw)

