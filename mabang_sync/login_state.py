"""浏览器资料迁移及登录状态持久化；不输出 Cookie / sessionStorage 内容。"""
import json
import logging
import shutil
import time
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4
from .storage import write_json


def prepare_profile(config):
    settings = config["browser"]
    profile = Path(settings["profile_dir"])
    Path(settings["login_info_dir"]).mkdir(parents=True, exist_ok=True)
    legacy = Path(settings["legacy_profile_dir"]) if settings["legacy_profile_dir"] else None
    if not profile.exists() and legacy and legacy.is_dir() and legacy.resolve() != profile.resolve():
        if legacy.resolve() in profile.resolve().parents:
            raise ValueError("新浏览器目录不能位于待迁移旧目录内部")
        # 完整复制成功后才启用，失败的临时副本不会被当成有效 profile。
        temporary = profile.with_name(profile.name + ".migrating-" + uuid4().hex[:8])
        profile.parent.mkdir(parents=True, exist_ok=True)
        logging.info("首次迁移浏览器资料：%s → %s（保留旧目录）", legacy, profile)
        shutil.copytree(legacy, temporary, ignore=shutil.ignore_patterns("Singleton*", "DevToolsActivePort", "lockfile"))
        temporary.rename(profile)
    profile.mkdir(parents=True, exist_ok=True)
    logging.info("浏览器固定资料目录：%s；登录快照目录：%s", profile, settings["login_info_dir"])


def state_path(config):
    return Path(config["browser"]["login_info_dir"]) / "login_state.json"


def origins(config):
    return {f"{p.scheme}://{p.netloc}" for p in
            (urlsplit(config["mabang"][key]) for key in ("home_url", "dashboard_url"))}


def read_state(config):
    path = state_path(config)
    if not path.exists():
        return {"cookies": [], "sessions": {}}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("cookies"), list) or not isinstance(value.get("sessions"), dict):
        raise ValueError("登录快照结构无效")
    return value


def cookie_key(cookie):
    return cookie.get("name"), cookie.get("domain"), cookie.get("path", "/"), json.dumps(cookie.get("partitionKey"), sort_keys=True)


def restore_login_state(tab, config):
    """在进入首页前补充 profile 中缺失的 Cookie，页面脚本执行前恢复同源 sessionStorage。"""
    try:
        state = read_state(config)
        existing = {cookie_key(c) for c in tab.cookies(all_domains=True, all_info=True)}
        allowed = {"name", "value", "domain", "path", "expires", "httpOnly", "secure", "sameSite", "priority", "sourceScheme", "sourcePort", "partitionKey"}
        cookies = []
        for cookie in state["cookies"]:
            if cookie_key(cookie) in existing:
                continue
            if cookie.get("expires", -1) > 0 and cookie["expires"] <= time.time():
                continue
            cookies.append({k: v for k, v in cookie.items() if k in allowed})
        if cookies:
            tab.browser.set.cookies(cookies)
        sessions = {origin: values for origin, values in state["sessions"].items() if origin in origins(config)}
        script_id = None
        if sessions:
            # 仅补缺失键；绝不把同源旧快照覆盖到浏览器里更新的会话值上。
            script = "(() => { const saved = " + json.dumps(sessions, ensure_ascii=True) + ";" + """
                const values = saved[location.origin];
                if (values && window === window.top) {
                    for (const [key, value] of Object.entries(values)) {
                        if (sessionStorage.getItem(key) === null) sessionStorage.setItem(key, value);
                    }
                }
                })();
            """
            script_id = tab.add_init_js(script)
        logging.info("登录状态恢复：补充 Cookie=%s 个；sessionStorage 来源=%s 个；不输出内容", len(cookies), len(sessions))
        return script_id
    except Exception as exc:
        logging.warning("登录快照恢复失败：%s；继续使用固定浏览器资料及正常登录流程", type(exc).__name__)
        return None


def save_login_state(tab, config):
    try:
        cookies = list(tab.cookies(all_domains=True, all_info=True))
        value = tab.run_js("return {origin: location.origin, values: Object.fromEntries(Object.entries(sessionStorage))};")
        try:
            sessions = read_state(config)["sessions"]
        except (OSError, ValueError):
            sessions = {}
        if value["origin"] in origins(config):
            sessions[value["origin"]] = value["values"]
        write_json(state_path(config), {"cookies": cookies, "sessions": sessions})
        logging.info("登录状态已保存：Cookie=%s 个；sessionStorage 来源=%s 个；文件=%s", len(cookies), len(sessions), state_path(config))
    except Exception as exc:
        logging.warning("登录快照保存失败：%s；Chrome 用户目录仍保留原生持久化资料", type(exc).__name__)
