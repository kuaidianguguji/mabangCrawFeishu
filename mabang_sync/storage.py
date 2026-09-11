import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from .collector import MODULES
from .cleaners import clean_module


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


class FileSink:
    """本地输出适配器；后续飞书上传器读取 cleaned/ 即可。"""

    def __init__(self, root):
        self.collected_at = datetime.now(timezone.utc).isoformat()
        self.path = Path(root) / (datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid4().hex[:8])
        self.path.mkdir(parents=True)

    def save_raw(self, name, body):
        write_json(self.path / "raw" / f"{name}.json", body)

    def finish(self, captured, errors):
        statuses = {}
        for name, body in captured.items():
            cleaned = clean_module(name, body)
            cleaned["run_started_at"] = self.collected_at
            write_json(self.path / "cleaned" / f"{name}.json", cleaned)
            statuses[name] = {"status": cleaned["status"], "warnings": cleaned["warnings"],
                              "row_counts": {k: len(v) for k, v in cleaned["tables"].items()}}
        missing = [n for n in MODULES if n not in captured]
        report = {"run_started_at": self.collected_at, "capture_complete": not missing,
                  "cleaning_complete": not missing and all(v["status"] == "cleaned" for v in statuses.values()),
                  "missing_modules": missing, "errors": errors, "modules": statuses}
        write_json(self.path / "report.json", report)
        return report
