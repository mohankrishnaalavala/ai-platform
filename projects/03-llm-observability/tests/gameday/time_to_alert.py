"""Time to alert, per game-day experiment (gameday.sh runs it at the end).
  python3 time_to_alert.py EVENTS ALERTS
EVENTS: gameday.sh's JSON lines, {"experiment": ..., "started_at": <unix s>, "expects": <regex for the alert name>}.
ALERTS: the alert receiver's GET /log, JSON lines {"received_at": <unix s>, "status": "firing|resolved",
"alertname": ..., "labels": {...}, "startsAt": ...}.
For each experiment, the first firing alert received at or after its start whose name matches what it expects.
Alert names differ per experiment, so a match may come after the next experiment started. No match: [X].
Before each experiment, gameday.sh also runs (stdin: Prometheus's answer to the query ALERTS):
  python3 time_to_alert.py clear NAME                    exit 0 once alert NAME is neither pending nor firing
  python3 time_to_alert.py record SETTINGS EXPERIMENT    note in SETTINGS which alerts fire as EXPERIMENT starts
"""
import json
import re
import sys
from datetime import datetime, timezone


def utc(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%H:%M:%SZ")


def time_to_alert(events, alerts):
    """[(experiment, started_at, alertname or None, received_at or None, minutes or None)], in start order."""
    firing = sorted((a for a in alerts if a.get("status") == "firing"), key=lambda a: a["received_at"])
    rows = []
    for e in sorted(events, key=lambda e: e["started_at"]):
        hit = next((a for a in firing if a["received_at"] >= e["started_at"]
                    and re.search(e["expects"], a.get("alertname", ""), re.IGNORECASE)), None)
        rows.append((e["experiment"], e["started_at"], hit and hit["alertname"], hit and hit["received_at"],
                     hit and round((hit["received_at"] - e["started_at"]) / 60, 1)))
    return rows


def alert_names(answer, state=None):
    """Alert names in Prometheus's answer to the query ALERTS, optionally only those in one state."""
    result = (answer.get("data") or {}).get("result", [])
    return sorted({r["metric"]["alertname"] for r in result if state in (None, r["metric"].get("alertstate"))})


def record(settings_path, experiment, answer):
    with open(settings_path) as f:
        settings = json.load(f)
    settings.setdefault("firing_at_start", {})[experiment] = alert_names(answer, "firing")
    with open(settings_path, "w") as f:
        json.dump(settings, f, indent=1)


def table(rows):
    out = ["| Experiment | Started | First matching alert | Received | Time to alert |", "|---|---|---|---|---|"]
    for name, started, alert, received, minutes in rows:
        out.append(f"| {name} | {utc(started)} | {alert or '[X]'} | {utc(received) if received else '[X]'} | "
                   f"{f'{minutes} min' if minutes is not None else '[X]'} |")
    return "\n".join(out)


def read_lines(path):
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


if __name__ == "__main__":
    if sys.argv[1] == "clear":
        sys.exit(sys.argv[2] in alert_names(json.loads(sys.stdin.read() or "{}")))
    elif sys.argv[1] == "record":
        record(sys.argv[2], sys.argv[3], json.loads(sys.stdin.read() or "{}"))
    else:
        print(table(time_to_alert(read_lines(sys.argv[1]), read_lines(sys.argv[2]))))
