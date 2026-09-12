"""可安全输出的业务异常与日志文本脱敏。"""
import json


class FeishuError(ValueError):
    """已经脱敏、可以向终端和常驻日志展示的飞书错误。"""


def safe_text(value, config=None, secrets=()):
    hidden = [str(v) for v in secrets if v]

    def collect(values):
        for key, item in values.items():
            if isinstance(item, dict):
                collect(item)
            elif key in {"password", "username", "app_secret", "app_token", "app_id", "tenant_access_token"} and item:
                hidden.append(str(item))

    if config:
        collect(config)
    text = str(value)
    for secret in sorted(set(hidden), key=len, reverse=True):
        for form in (secret, json.dumps(secret, ensure_ascii=False)[1:-1], json.dumps(secret)[1:-1]):
            text = text.replace(form, "<已隐藏>")
    return text.replace("\r", "\\r").replace("\n", "\\n")
