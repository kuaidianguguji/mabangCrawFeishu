from pathlib import Path
import os
import tomllib


def load_config(path: Path) -> dict:
    path = path.resolve()
    template = Path(__file__).resolve().parent.parent / "config.example.toml"
    config = tomllib.loads(template.read_text(encoding="utf-8"))
    supplied = tomllib.loads(path.read_text(encoding="utf-8-sig"))
    for section, values in supplied.items():
        if section not in config or not isinstance(values, dict):
            raise ValueError(f"未知配置节: {section}")
        if set(values) - set(config[section]):
            raise ValueError(f"{section} 包含未知配置项")
        config[section].update(values)
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
