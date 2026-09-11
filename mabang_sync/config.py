from pathlib import Path
import os
import tomllib
from zoneinfo import ZoneInfo


def merge_config(defaults, supplied, prefix=""):
    for key, value in supplied.items():
        if key not in defaults:
            raise ValueError(f"未知配置项: {prefix}{key}")
        if isinstance(defaults[key], dict):
            if not isinstance(value, dict):
                raise ValueError(f"{prefix}{key} 必须为配置节")
            merge_config(defaults[key], value, f"{prefix}{key}.")
        else:
            if type(value) is not type(defaults[key]) and not (
                type(defaults[key]) in (int, float) and type(value) in (int, float)
            ):
                raise ValueError(f"配置类型错误: {prefix}{key}")
            defaults[key] = value


def load_config(path: Path) -> dict:
    path = path.resolve()
    template = Path(__file__).resolve().parent.parent / "config.example.toml"
    config = tomllib.loads(template.read_text(encoding="utf-8"))
    supplied = tomllib.loads(path.read_text(encoding="utf-8-sig"))
    merge_config(config, supplied)
    for key in ("app_id", "app_secret"):
        config["feishu"][key] = os.getenv(f"FEISHU_{key.upper()}", config["feishu"][key])
    ZoneInfo(config["feishu"]["sync"]["timezone"])
    if config["feishu"]["timeout"] <= 0:
        raise ValueError("feishu.timeout 必须大于零")
    for key in ("username", "password"):
        config["account"][key] = os.getenv(f"MABANG_{key.upper()}", config["account"][key])
    for section, key in (("browser", "profile_dir"), ("output", "directory")):
        config[section][key] = str((path.parent / config[section][key]).resolve())
    for key, value in config["timing"].items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            raise ValueError(f"timing.{key} 必须为非负数")
        if key != "retry_count" and value == 0:
            raise ValueError(f"timing.{key} 必须大于零")
    if type(config["timing"]["retry_count"]) is not int:
        raise ValueError("retry_count 必须为整数")
    if type(config["browser"]["port"]) is not int or not 1024 <= config["browser"]["port"] <= 65535:
        raise ValueError("browser.port 必须为 1024–65535 整数")
    if not config["mabang"]["logged_in_xpath"]:
        config["mabang"]["logged_in_xpath"] = '//div[@id="mb-user"]'
    return config
