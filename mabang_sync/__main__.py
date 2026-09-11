import argparse
import json
import logging
from pathlib import Path
from .collector import MODULES, collect, validate_response
from .config import load_config
from .storage import FileSink


def main():
    parser = argparse.ArgumentParser(description="马帮看板采集与离线清洗")
    sub = parser.add_subparsers(dest="command", required=True)
    live = sub.add_parser("collect", help="浏览器登录、监听并清洗")
    live.add_argument("--config", type=Path, default=Path("config.toml"))
    offline = sub.add_parser("clean", help="清洗目录中的 8 个 JSON，无需账号或浏览器")
    offline.add_argument("--input", type=Path, required=True)
    offline.add_argument("--output", type=Path, default=Path("output"))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    browser = None
    try:
        if args.command == "clean":
            sink = FileSink(args.output)
            captured, errors = {}, {}
            for name in MODULES:
                try:
                    body = validate_response(json.loads((args.input / f"{name}.json").read_text(encoding="utf-8-sig")))
                    sink.save_raw(name, body)
                    captured[name] = body
                except (OSError, ValueError) as exc:
                    errors[name] = type(exc).__name__
            report = sink.finish(captured, errors)
        else:
            from .browser import open_browser, ensure_login
            config = load_config(args.config)
            sink = FileSink(config["output"]["directory"])
            browser, tab = open_browser(config)
            ensure_login(tab, config)
            captured, errors = collect(tab, config, sink.save_raw)
            report = sink.finish(captured, errors)
        print(f"输出目录: {sink.path.resolve()}")
        print(f"接口完整: {report['capture_complete']}；清洗完整: {report['cleaning_complete']}")
        return 0 if report["cleaning_complete"] else 2
    except (OSError, ValueError, RuntimeError) as exc:
        logging.error("运行失败: %s", exc)
        return 1
    finally:
        if browser is not None and config["browser"]["close_on_exit"]:
            browser.quit()


if __name__ == "__main__":
    raise SystemExit(main())
