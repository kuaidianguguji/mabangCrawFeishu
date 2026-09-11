import argparse
import json
import logging
from pathlib import Path
from .collector import MODULES, collect, validate_response
from .config import load_config
from .storage import FileSink, write_json
from .feishu_plan import build_plan


def read_responses(folder):
    captured, errors = {}, {}
    for name in MODULES:
        try:
            captured[name] = validate_response(json.loads((folder / f"{name}.json").read_text(encoding="utf-8-sig")))
        except (OSError, ValueError) as exc:
            errors[name] = type(exc).__name__
    return captured, errors


def export_feishu(captured, config, folder, business_date=None, write=False):
    plan = build_plan(captured, config, business_date)
    write_json(folder / "feishu_plan.json", plan)
    if write:
        logging.info("飞书上传已启用，准备检查字段并同步 %s 条日记录", len(plan["records"]))
        from .feishu import FeishuClient, sync_plan
        result = sync_plan(FeishuClient(config), plan, config,
                           progress=lambda value: write_json(folder / "feishu_sync_report.json", value))
        counts = {}
        for action in result["actions"]:
            kind = action["action"]
            counts[kind] = counts.get(kind, 0) + 1
        logging.info("飞书同步结果：%s；完整: %s", counts, result["complete"])
        return result["complete"]
    logging.info("飞书未上传：当前仅生成预览（collect 请设置 feishu.enabled=true；离线命令请加 --write）")
    return not bool(plan["errors"])


def run_collection(config):
    """一次采集任务，供手动命令与常驻调度共用。"""
    from .browser import open_browser, ensure_login
    browser = None
    try:
        sink = FileSink(config["output"]["directory"])
        browser, tab = open_browser(config)
        ensure_login(tab, config)
        captured, errors = collect(tab, config, sink.save_raw)
        report = sink.finish(captured, errors)
        feishu_complete = export_feishu(captured, config, sink.path, write=config["feishu"]["enabled"])
        if not feishu_complete:
            logging.warning("飞书映射或同步未全部完成，请查看 feishu_plan.json / feishu_sync_report.json")
        logging.info("输出目录: %s", sink.path.resolve())
        logging.info("接口完整: %s；清洗完整: %s", report["capture_complete"], report["cleaning_complete"])
        return 0 if report["cleaning_complete"] and feishu_complete else 2
    finally:
        if browser is not None and config["browser"]["close_on_exit"]:
            browser.quit()


def main():
    parser = argparse.ArgumentParser(description="马帮看板采集与离线清洗")
    sub = parser.add_subparsers(dest="command", required=True)
    live = sub.add_parser("collect", help="浏览器登录、监听并清洗")
    live.add_argument("--config", type=Path, default=Path("config.toml"))
    scheduled = sub.add_parser("schedule", help="常驻运行，按配置的北京时间每日执行")
    scheduled.add_argument("--config", type=Path, default=Path("config.toml"))
    offline = sub.add_parser("clean", help="清洗目录中的 8 个 JSON，无需账号或浏览器")
    offline.add_argument("--input", type=Path, required=True)
    offline.add_argument("--output", type=Path, default=Path("output"))
    sync = sub.add_parser("feishu", help="生成六表写入预览；--write 才会写飞书")
    sync.add_argument("--input", type=Path, required=True, help="运行目录或 raw 目录")
    sync.add_argument("--config", type=Path, default=Path("config.toml"))
    sync.add_argument("--date", help="显式指定源数据统计日 YYYY-MM-DD；默认按源时间戳和配置时区")
    sync.add_argument("--write", action="store_true", help="实际写入已配置的飞书表")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        if args.command == "schedule":
            from .scheduler import run_scheduler
            return run_scheduler(args.config, run_collection)
        if args.command == "collect":
            return run_collection(load_config(args.config))
        if args.command == "feishu":
            config = load_config(args.config)
            folder = args.input / "raw" if (args.input / "raw").is_dir() else args.input
            captured, errors = read_responses(folder)
            if errors:
                logging.warning("部分源数据缺失或无效: %s", ", ".join(errors))
            target = folder.parent if folder.name == "raw" else folder
            complete = export_feishu(captured, config, target, args.date, args.write)
            print(f"飞书{'同步' if args.write else '预览'}完成，完整: {complete}；输出: {target.resolve()}")
            return 0 if complete else 2
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
        print(f"输出目录: {sink.path.resolve()}")
        print(f"接口完整: {report['capture_complete']}；清洗完整: {report['cleaning_complete']}")
        return 0 if report["cleaning_complete"] else 2
    except (OSError, ValueError, RuntimeError) as exc:
        logging.error("运行失败: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
