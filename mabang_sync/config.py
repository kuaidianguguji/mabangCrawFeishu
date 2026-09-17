from pathlib import Path
import os
import tomllib
import re
import math
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
    ZoneInfo(config["feishu"]["sync"]["business_timezone"])
    ZoneInfo(config["feishu"]["sync"]["source_timezone"])
    if config["feishu"]["timeout"] <= 0:
        raise ValueError("feishu.timeout 必须大于零")
    for key in ("username", "password"):
        config["account"][key] = os.getenv(f"MABANG_{key.upper()}", config["account"][key])
    for section, key in (("browser", "profile_dir"), ("browser", "login_info_dir"), ("output", "directory"), ("schedule", "state_dir")):
        config[section][key] = str((path.parent / config[section][key]).resolve())
    if config["browser"]["legacy_profile_dir"]:
        config["browser"]["legacy_profile_dir"] = str((path.parent / config["browser"]["legacy_profile_dir"]).resolve())
    for stage, policy in config["retry"].items():
        if type(policy["count"]) is not int or policy["count"] < 0:
            raise ValueError(f"retry.{stage}.count 必须为非负整数")
        value = policy["interval"]
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError(f"retry.{stage}.interval 必须为有限非负秒数")
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
    schedule = config["schedule"]
    if not schedule["times"] or any(not isinstance(v, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", v) for v in schedule["times"]):
        raise ValueError("schedule.times 需要至少一个北京时间 HH:MM，例如 [\"23:50\"]")
    if len(set(schedule["times"])) != len(schedule["times"]):
        raise ValueError("schedule.times 不允许重复时间")
    if not 1 <= schedule["poll_interval"] <= 60:
        raise ValueError("schedule.poll_interval 必须在 1～60 秒之间")
    return config
