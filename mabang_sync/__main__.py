import argparse
import json
import logging
from pathlib import Path
from .collector import MODULES, collect, validate_response
from .config import load_config
from .storage import FileSink, write_json
from .feishu_plan import build_plan
from .diagnostics import FeishuError, safe_text
from .retry import run_with_retry, retryable, CaptureIncompleteError


def read_responses(folder):
    captured, errors = {}, {}
    for name in MODULES:
        try:
            captured[name] = validate_response(json.loads((folder / f"{name}.json").read_text(encoding="utf-8-sig")))
        except (OSError, ValueError) as exc:
            errors[name] = type(exc).__name__
    return captured, errors


def export_feishu(captured, config, folder, business_date=None, write=False, check_only=False):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(folder / "feishu.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logging.getLogger().addHandler(handler)
    try:
        return _export_feishu(captured, config, folder, business_date, write, check_only)
    except Exception as exc:
        message = safe_text(exc, config)
        logging.error("飞书流程未完成：%s；详细日志=%s", message, (folder / "feishu.log").resolve())
        raise FeishuError(message, retryable=retryable(exc)) from None
    finally:
        logging.getLogger().removeHandler(handler)
        handler.close()


def _export_feishu(captured, config, folder, business_date, write, check_only):
    logging.info("飞书运行配置：enabled=%s；本次执行=%s；昨日比对=%s；历史趋势回补=%s",
                 config["feishu"]["enabled"], "只读检查" if check_only else "写入" if write else "本地预览",
                 config["feishu"]["sync"]["compare_yesterday"], config["feishu"]["sync"]["backfill_sales_trend"])
    plan = build_plan(captured, config, business_date)
    write_json(folder / "feishu_plan.json", plan)
    logging.info("飞书写入计划已保存：%s；记录=%s条；错误=%s；警告=%s",
                 (folder / "feishu_plan.json").resolve(), len(plan["records"]), safe_text(plan["errors"], config), safe_text(plan["warnings"], config))
    if write or check_only:
        logging.info("准备%s %s 条日记录", "只读检查" if check_only else "同步", len(plan["records"]))
        from .feishu import FeishuClient, sync_plan
        report_path = folder / ("feishu_check_report.json" if check_only else "feishu_sync_report.json")
        write_json(report_path, {"complete": False, "phase": "configuration", "actions": []})
        try:
            client = FeishuClient(config)
        except FeishuError as exc:
            write_json(report_path, {"complete": False, "phase": "configuration", "actions": [], "error": safe_text(exc, config)})
            raise
        try:
            result = sync_plan(client, plan, config,
                               progress=lambda value: write_json(report_path, value), check_only=check_only)
        finally:
            client.session.close()
        counts = {}
        for action in result["actions"]:
            kind = action["action"]
            counts[kind] = counts.get(kind, 0) + 1
        logging.info("飞书%s结果：%s；完整: %s；报告=%s", "检查" if check_only else "同步", counts, result["complete"], report_path.resolve())
        return result["complete"]
    logging.info("飞书未上传：当前仅生成预览（collect 请设置 feishu.enabled=true；离线命令请加 --write）")
    return not bool(plan["errors"])


def run_collection(config):
    """获取前整体重试重建浏览器；获取后整体重试固定本批 JSON。"""
    from .browser import open_browser, ensure_login
    from .login_state import restore_login_state, save_login_state

    def acquire():
        browser, tab, restore_script = None, None, None
        success = False
        sink = FileSink(config["output"]["directory"])
        logging.info("本次采集尝试输出目录：%s", sink.path.resolve())
        try:
            browser, tab = open_browser(config)
            restore_script = restore_login_state(tab, config)
            ensure_login(tab, config)
            if restore_script:
                tab.remove_init_js(restore_script)
                restore_script = None
            save_login_state(tab, config)
            # 完整 JSON 先保留在内存；后续落盘失败归入获取后重试，不能重新采集。
            captured, errors = collect(tab, config, lambda name, body: None,
                                       save_diagnostics=lambda report: write_json(sink.path / "capture_diagnostics.json", report))
            if len(captured) != len(MODULES):
                for name, body in captured.items():
                    sink.save_raw(name, body)
                sink.finish(captured, errors)
                missing = ", ".join(n for n in MODULES if n not in captured)
                raise CaptureIncompleteError(f"八个接口尚未收齐；缺少 {missing}；详情见 {sink.path / 'capture_diagnostics.json'}")
            success = True
            return sink, captured, errors
        except Exception as exc:
            write_json(sink.path / "capture_failure.json", {"stage": "before_capture", "error_type": type(exc).__name__})
            raise
        finally:
            if tab is not None:
                if restore_script:
                    try:
                        tab.remove_init_js(restore_script)
                    except Exception:
                        pass
                save_login_state(tab, config)
            if browser is not None and (not success or config["browser"]["close_on_exit"]):
                try:
                    logging.info("程序主动关闭本次专用浏览器：%s", "采集尝试失败，释放浏览器以供整体重试重新打开；不是检测到浏览器崩溃"
                                 if not success else "采集已完成，close_on_exit=true")
                    browser.quit()
                except Exception as exc:
                    logging.warning("关闭本次专用浏览器失败：%s", type(exc).__name__)

    sink, captured, errors = run_with_retry(config, "before_capture", acquire, "获取数据前整体流程")

    def process():
        for name, body in captured.items():
            sink.save_raw(name, body)
        report = sink.finish(captured, errors)
        feishu_complete = export_feishu(captured, config, sink.path, write=config["feishu"]["enabled"])
        if not feishu_complete:
            logging.warning("飞书映射或同步未全部完成，请查看 feishu_plan.json / feishu_sync_report.json")
        logging.info("输出目录: %s", sink.path.resolve())
        logging.info("接口完整: %s；清洗完整: %s", report["capture_complete"], report["cleaning_complete"])
        return 0 if report["cleaning_complete"] and feishu_complete else 2
    return run_with_retry(config, "after_capture", process, "获取数据后整体流程（复用已获取 JSON）")


def main():
    parser = argparse.ArgumentParser(description="马帮看板采集与离线清洗")
    sub = parser.add_subparsers(dest="command", required=True)
    live = sub.add_parser("collect", help="浏览器登录、监听并清洗")
    live.add_argument("--config", type=Path, default=Path("config.toml"))
    scheduled = sub.add_parser("schedule", help="常驻运行，按配置的北京时间每日执行")
    scheduled.add_argument("--config", type=Path, default=Path("config.toml"))
    scheduled.add_argument("--now", action="store_true", help="立即运行一次，完成后继续按北京时间定时运行")
    offline = sub.add_parser("clean", help="清洗目录中的 8 个 JSON，无需账号或浏览器")
    offline.add_argument("--input", type=Path, required=True)
    offline.add_argument("--output", type=Path, default=Path("output"))
    sync = sub.add_parser("feishu", help="生成六表写入预览；--write 才会写飞书")
    sync.add_argument("--input", type=Path, required=True, help="运行目录或 raw 目录")
    sync.add_argument("--config", type=Path, default=Path("config.toml"))
    sync.add_argument("--date", help="显式指定源数据统计日 YYYY-MM-DD；默认按源时间戳和配置时区")
    sync_mode = sync.add_mutually_exclusive_group()
    sync_mode.add_argument("--write", action="store_true", help="实际写入已配置的飞书表")
    sync_mode.add_argument("--check", action="store_true", help="联网只读检查字段与记录差异，不写飞书")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        if args.command == "schedule":
            from .scheduler import run_scheduler
            return run_scheduler(args.config, run_collection, run_now=args.now)
        if args.command == "collect":
            return run_collection(load_config(args.config))
        if args.command == "feishu":
            config = load_config(args.config)
            folder = args.input / "raw" if (args.input / "raw").is_dir() else args.input
            captured, errors = read_responses(folder)
            if errors:
                logging.warning("部分源数据缺失或无效: %s", ", ".join(errors))
            target = folder.parent if folder.name == "raw" else folder
            complete = run_with_retry(config, "after_capture",
                                      lambda: export_feishu(captured, config, target, args.date, args.write, args.check),
                                      "离线数据后续流程")
            print(f"飞书{'同步' if args.write else '检查' if args.check else '预览'}完成，完整: {complete}；输出: {target.resolve()}")
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
