"""Load config.yaml and apply PAPERBOT__SECTION__KEY environment overrides."""
from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent


def _apply_env(cfg: dict, environ: dict | None = None) -> dict:
    environ = os.environ if environ is None else environ
    for key, raw in environ.items():
        if not key.startswith("PAPERBOT__"):
            continue
        parts = [p.lower() for p in key[len("PAPERBOT__"):].split("__") if p]
        node = cfg
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        node[parts[-1]] = yaml.safe_load(raw)
    return cfg


def load_config(path: str | os.PathLike | None = None, environ: dict | None = None) -> dict[str, Any]:
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except Exception:
        pass
    path = Path(path or os.environ.get("PAPERBOT_CONFIG", ROOT / "config.yaml"))
    with open(path) as fh:
        cfg = yaml.safe_load(fh) or {}
    cfg = _apply_env(copy.deepcopy(cfg), environ)
    # resolve relative paths against project root
    for section, keys in {"storage": ["data_dir", "sqlite", "trades_csv", "state_file"],
                          "logging": ["file"], "model": ["dir"]}.items():
        for k in keys:
            v = cfg.get(section, {}).get(k)
            if v and not os.path.isabs(v):
                cfg[section][k] = str(ROOT / v)
    Path(cfg["storage"]["data_dir"]).mkdir(parents=True, exist_ok=True)
    Path(cfg["model"]["dir"]).mkdir(parents=True, exist_ok=True)
    return cfg
