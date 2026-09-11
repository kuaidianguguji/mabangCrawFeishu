"""北京时间日程循环；不依赖操作系统本地时区，不创建系统计划任务。"""
import json
import logging
from datetime import datetime, time as day_time, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path
import time
from zoneinfo import ZoneInfo
from filelock import FileLock, Timeout
from .config import load_config
from .storage import write_json

BEIJING = ZoneInfo("Asia/Shanghai")


def now_beijing():
    return datetime.now(BEIJING)


def next_run(now, times, last_slot=None):
    """选下一个未触发时刻；启动时不补跑已过时刻。"""
    if now.tzinfo is None:
        raise ValueError("调度时间必须带时区")
    now = now.astimezone(BEIJING)
    floor = now
    if last_slot:
        previous = datetime.fromisoformat(last_slot)
        if previous.tzinfo is None:
            raise ValueError("调度状态时间缺少时区")
        floor = max(floor, previous.astimezone(BEIJING) + timedelta(microseconds=1))
    day = floor.date()
    for offset in (0, 1):
        for value in sorted(times):
            candidate = datetime.combine(day + timedelta(days=offset), day_time.fromisoformat(value), BEIJING)
            if candidate >= floor:
                return candidate
    raise RuntimeError("无法确定下一次执行时间")


def run_slot(slot, state_path, config_path, runner, clock=now_beijing):
    # 先记录再执行：即使进程崩溃或重启，也不会重复自动提交同一批次。
    state = {"last_slot": slot.isoformat(), "started_at": clock().isoformat(), "status": "running"}
    write_json(state_path, state)
    logging.info("北京时间 %s：开始采集与同步", slot.strftime("%Y-%m-%d %H:%M"))
    try:
        config = load_config(config_path)
        if not config["browser"]["close_on_exit"]:
            raise ValueError("常驻运行需要 browser.close_on_exit=true")
        code = runner(config)
        state.update(status="success" if code == 0 else "incomplete" if code == 2 else "failed", exit_code=code)
        logging.info("本次定时运行结束，退出码: %s", code)
    except KeyboardInterrupt:
        state["status"] = "interrupted"
        raise
    except Exception as exc:
        state.update(status="failed", error_type=type(exc).__name__)
        logging.error("本次定时运行失败（%s），常驻进程将继续等待下一时刻", type(exc).__name__)
    finally:
        state["finished_at"] = clock().isoformat()
        write_json(state_path, state)
    return state


def run_scheduler(config_path, runner, clock=now_beijing, sleep=time.sleep):
    config_path = Path(config_path).resolve()
    config = load_config(config_path)
    if not config["browser"]["close_on_exit"]:
        raise ValueError("常驻运行需要 browser.close_on_exit=true，以便下一次打开同一浏览器环境")
    schedule = config["schedule"]
    folder = Path(schedule["state_dir"])
    folder.mkdir(parents=True, exist_ok=True)
    lock = FileLock(str(folder / "scheduler.lock"))
    try:
        lock.acquire(timeout=0)
    except Timeout:
        raise RuntimeError("已有常驻进程使用此调度目录，请勿重复启动") from None
    handler = None
    try:
        handler = RotatingFileHandler(folder / "scheduler.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
        class BeijingFormatter(logging.Formatter):
            def formatTime(self, record, datefmt=None):
                return datetime.fromtimestamp(record.created, BEIJING).strftime("%Y-%m-%d %H:%M:%S +08:00")
        handler.setFormatter(BeijingFormatter("%(asctime)s %(levelname)s %(message)s"))
        logging.getLogger().addHandler(handler)
        state_path = folder / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
        if not isinstance(state, dict):
            raise ValueError("调度状态文件格式错误")
        last_slot = state.get("last_slot")
        if state.get("status") == "running":
            logging.warning("上次任务可能被强制中断；不自动重跑该时刻，请检查输出报告")
        logging.info("常驻模式启动；北京时间每日 %s；按 Ctrl+C 停止", "、".join(sorted(schedule["times"])))
        target = next_run(clock(), schedule["times"], last_slot)
        logging.info("下次执行：%s（北京时间）", target.strftime("%Y-%m-%d %H:%M:%S"))
        while True:
            now = clock().astimezone(BEIJING)
            seconds = (target - now).total_seconds()
            if seconds > 0:
                sleep(min(seconds, schedule["poll_interval"]))
                continue
            if target.date() < now.date():
                # 跨日休眠恢复后不把今天的数据错当成昨天定时结果。
                logging.warning("已跨日，跳过错过的定时时刻 %s", target.isoformat())
            else:
                run_slot(target, state_path, config_path, runner, clock)
                last_slot = target.isoformat()
            target = next_run(clock(), schedule["times"], last_slot)
            logging.info("下次执行：%s（北京时间）", target.strftime("%Y-%m-%d %H:%M:%S"))
    except KeyboardInterrupt:
        logging.info("常驻运行已停止")
        return 0
    finally:
        if handler:
            logging.getLogger().removeHandler(handler)
            handler.close()
        lock.release()
