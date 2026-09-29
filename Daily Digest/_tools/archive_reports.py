"""Archive Automation reports locally; never modify or rerun source automations.

Usage: python archive_reports.py [--from-date YYYY-MM-DD] [--to-date YYYY-MM-DD]
The default window is the last calendar month in America/Los_Angeles.
Use --refresh-index after moving reports into the read folder.
Use --mark-read FILE [FILE ...] to acknowledge all saved reports in those date files.
The local reader navigates dates, reports, and articles, and saves reading states and favorites.
"""

from __future__ import annotations

import argparse
import calendar
import errno
import hashlib
import html
import json
import msvcrt
import re
import sqlite3
import sys
from collections import defaultdict
from contextlib import closing, contextmanager
from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache
from pathlib import Path, PureWindowsPath
from urllib.parse import quote, urlencode, urlparse
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup, NavigableString, SoupStrainer
from markdown_it import MarkdownIt


ROOT = Path(__file__).resolve().parent.parent
COPILOT = Path.home() / ".copilot"
ZONE = ZoneInfo("America/Los_Angeles")
MARKER = "[weekly-ai-briefing-archive:v1]"
SCHEMA_VERSION = 7
READ_DIR = "已读"
TERMINAL = {"completed", "failed", "cancelled", "canceled", "skipped"}
ACTIVE = {"running", "pending", "queued"}
MD = MarkdownIt("gfm-like", {"html": False})
DATE_FILE = re.compile(r"\d{4}-\d{2}-\d{2}\.html")
SOURCE_REF = re.compile(
    r"(?<![\w/])(?:[A-Za-z]:\\[^\s<>\"|]+|"
    r"(?:[\w.@-]+[/\\])+[\w.@-]+\."
    r"(?:cs|cpp|h|py|ts|tsx|js|jsx|jsonl?|md|sql|ya?ml|ps1|sh|txt)"
    r"(?::\d+(?:-\d+)?)?)"
)
FILE_NAME = re.compile(
    r"[\w.@/\\-]+\.(?:cs|cpp|h|py|ts|tsx|js|jsx|jsonl?|md|sql|ya?ml|ps1|sh|txt)"
    r"(?::\d+(?:-\d+)?)?"
)
CSS = """
:root{color-scheme:light;--ink:#17243b;--muted:#62718a;--blue:#245bda;
--line:#dce4ef;--bg:#f2f5fa;--card:#fff;--warn:#fff3db}
*{box-sizing:border-box}html{scroll-behavior:smooth;scroll-padding-top:24px}
[hidden]{display:none!important}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.8 "Segoe UI",
"Microsoft YaHei",sans-serif}a{color:var(--blue);text-decoration:none;
overflow-wrap:anywhere}a:hover{text-decoration:underline}
header{background:linear-gradient(120deg,#142646,#234c81);color:#fff;padding:44px 5vw}
header a{color:#d9e9ff}h1{font-size:clamp(30px,4vw,48px);margin:8px 0}
header p{max-width:1000px;color:#dce8f8;margin:10px 0}.eyebrow{letter-spacing:2px;
font-size:12px;font-weight:700;text-transform:uppercase}
main{max-width:1320px;margin:auto;padding:26px 28px 60px}
.metrics{display:flex;flex-wrap:wrap;gap:12px;margin:20px 0}
.metric{background:#ffffff14;border:1px solid #ffffff30;border-radius:12px;
padding:12px 22px}.metric strong{font-size:27px;display:block}
.metric span{font-size:13px;color:#dae7fa}
.card,article{background:var(--card);border:1px solid var(--line);
border-radius:16px;padding:24px;margin-bottom:22px}
.notice{border-left:4px solid #e5a42d;background:var(--warn);padding:14px 18px;
border-radius:6px;margin:18px 0;font-size:14px}
.muted,.meta{color:var(--muted);font-size:13px}.meta a{margin-right:12px}
.digest-list{display:grid;grid-template-columns:minmax(0,1fr);gap:24px;margin-top:24px}
.digest{padding:26px;border:1px solid var(--line);border-left:4px solid var(--blue);
border-radius:14px;background:#fff;min-width:0}
.digest-heading{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}
.digest h3{font-size:24px;line-height:1.45;margin:0}
.summary-points{display:grid;gap:14px;list-style:none;padding:0;margin:22px 0;
counter-reset:summary-point}
.summary-points>li{position:relative;padding:16px 18px 16px 54px;border:1px solid #e0e8f5;
border-radius:10px;background:#f5f8ff;font-size:17px;line-height:1.9;overflow-wrap:anywhere}
.summary-points>li::before{counter-increment:summary-point;content:counter(summary-point,decimal-leading-zero);
position:absolute;left:15px;top:18px;font-size:12px;font-weight:700;line-height:25px;
width:25px;text-align:center;border-radius:7px;background:#245bda;color:#fff}
.summary-points p{margin:8px 0}.summary-points p:first-child{margin-top:0}
.summary-points p:last-child{margin-bottom:0}
.summary-points strong{color:#173f80;background:#e4edff;border-radius:4px;
padding:1px 3px;box-decoration-break:clone}
.summary-point-title{font-size:19px;font-weight:700;margin-bottom:8px}
.summary-label{font-size:14px;font-weight:600;color:#62718a;margin-right:5px}
.digest-more{display:inline-block;font-size:15px;font-weight:600;padding:5px 0}
.toc{display:flex;flex-wrap:wrap;gap:10px}.toc a{background:#eaf0ff;
padding:8px 13px;border-radius:8px;font-size:14px}
h2{font-size:25px;margin:12px 0 18px}h3{font-size:21px}h4{font-size:18px}
.report-body h1{font-size:27px}.report-body h2{font-size:23px;
border-bottom:1px solid var(--line);padding-bottom:8px;margin-top:34px}
.report-body h3{font-size:20px;margin-top:26px}
.report-body{overflow-wrap:anywhere}.report-body p{margin:14px 0}
.badge{display:inline-block;border-radius:20px;padding:3px 10px;background:#eaf4ed;
color:#23673a;font-size:12px;margin-left:8px}.badge.warn{background:#fff0da;color:#95550a}
.badge.unread{background:#eaf0ff;color:#245bda}
.article-unit{margin-top:24px;padding-bottom:20px;border-bottom:1px solid var(--line)}
.article-unit:last-child{border-bottom:0}
.article-reading-status{margin-top:18px}
.favorite-reason{padding:12px 16px;margin:12px 0;background:#fff9e8;
border-left:3px solid #d69a24;border-radius:6px;overflow-wrap:anywhere}
.favorite-reason p{margin:6px 0}
.reader-focused>header,.reader-focused main>.toc,.reader-focused #daily-overview,
.reader-focused #coverage,.reader-focused main>footer{display:none}
.reader-focused main{max-width:1100px}
.reader-summary-list{list-style:none;padding:0;display:grid;gap:14px}
.reader-summary-list>li{padding:16px;border:1px solid var(--line);border-radius:10px}
.reader-summary-list a{font-weight:600;font-size:17px}
.reader-summary-actions{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:10px}
.article-controls{display:flex;align-items:center;flex-wrap:wrap;gap:10px;
padding:12px 0;margin:10px 0;border-block:1px solid var(--line)}
.report-reading-button,.article-favorite-button{font:inherit;font-size:14px;border:1px solid #bdcff5;
border-radius:8px;background:#edf3ff;color:#245bda;padding:8px 13px;cursor:pointer}
.report-reading-button:disabled,.article-favorite-button:disabled{opacity:.5;cursor:wait}
.report-reading-button:focus-visible,.article-favorite-button:focus-visible{outline:3px solid #9bbdff;outline-offset:3px}
.article-favorite-button[aria-pressed="true"]{background:#fff4dc;border-color:#e6cf97;color:#946413}
table{width:100%;border-collapse:collapse;font-size:14px}
th,td{padding:11px 14px;border:1px solid var(--line);text-align:left;vertical-align:top}
th{background:#edf2fa}tr:nth-child(even) td{background:#fafcff}
.table-wrap{overflow-x:auto;margin:18px 0}
code{font:0.88em/1.65 Consolas,"Cascadia Code",monospace;background:#edf1f7;
border-radius:4px;padding:2px 5px}
pre{background:#142137;color:#e7edf7;padding:18px;border-radius:10px;overflow:auto;
white-space:pre;font-size:13px}pre code{background:none;color:inherit;padding:0}
blockquote{border-left:4px solid #98b3e8;margin:18px 0;padding:1px 18px;
color:#475e80;background:#f4f7fc}
details{margin-top:18px;border-top:1px solid var(--line);padding-top:14px}
summary{cursor:pointer;font-weight:600;color:#35558a}
.source-link{font-size:0.85em}hr{border:0;border-top:1px solid var(--line);margin:25px 0}
.empty{color:var(--muted);background:#f7f8fb}
footer{color:var(--muted);font-size:13px;padding-top:22px}
@media(max-width:700px){main{padding:18px 12px}.card,article{padding:17px}
header{padding:28px 20px}.metric{padding:10px 15px}th,td{padding:8px}
.digest{padding:18px 14px}.digest h3{font-size:21px}
.summary-points>li{padding:14px 12px 14px 45px;font-size:16px}
.summary-points>li::before{left:10px;top:16px}.summary-point-title{font-size:18px}}
@media print{body{background:#fff;font-size:11pt}header{background:#fff;color:#000;
padding:0}header p,header a,.metric span{color:#333}main{padding:0}
.metric{border:1px solid #ccc}.toc{display:none}.card,article{border-radius:0;
box-shadow:none;break-inside:auto}a{color:#123c85}pre{white-space:pre-wrap}
details{display:block}header,article>h2{break-after:avoid}}
"""


def stamp(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError(f"Source timestamp has no timezone: {value}")
    return result


def month_before(value: date) -> date:
    year, month = (value.year - 1, 12) if value.month == 1 else (value.year, value.month - 1)
    return date(year, month, min(value.day, calendar.monthrange(year, month)[1]))


def local_time(value: str | None) -> str:
    return stamp(value).astimezone(ZONE).strftime("%Y-%m-%d %H:%M %Z") if value else "未记录"


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def source_url(run: dict) -> str:
    return f"ghapp://sessions/{run['session_id']}" if run.get("session_id") else "#coverage"


def read_ledger(copilot: Path, first: date, last: date,
                *, excluded_task_ids: tuple[str, ...] = ()) -> tuple[list[dict], list[dict]]:
    uri = (copilot / "data.db").as_uri() + "?mode=ro"
    lower = datetime.combine(first, time.min, ZONE).astimezone(timezone.utc).isoformat()
    upper = datetime.combine(last + timedelta(days=1), time.min, ZONE).astimezone(timezone.utc).isoformat()
    with closing(sqlite3.connect(uri, uri=True, timeout=10)) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        workflows = [dict(row) for row in db.execute(
            "SELECT id,name,model,prompt,enabled,interval,schedule_hour,schedule_minute,"
            "schedule_day,created_at FROM workflows"
        )]
        excluded = set(excluded_task_ids) | {w["id"] for w in workflows if MARKER in w["prompt"]}
        sources = {w["id"]: w for w in workflows if w["id"] not in excluded}
        rows = [dict(row) for row in db.execute(
            """WITH dated AS (
                 SELECT id,task_id,session_id,workspace_id,status,trigger,started_at,
                        completed_at,error_message,archived_at,taken_over_at,
                        LEAD(started_at) OVER (
                          PARTITION BY session_id ORDER BY started_at
                        ) AS next_session_run
                 FROM workflow_runs
               )
               SELECT * FROM dated
               WHERE julianday(started_at)>=julianday(?)
                 AND julianday(started_at)<julianday(?)
               ORDER BY started_at,id""", (lower, upper)
        )]
    runs = []
    for row in rows:
        if row["task_id"] in excluded:
            continue
        workflow = sources.get(row["task_id"])
        row["name"] = workflow["name"] if workflow else f"历史 Automation（{row['task_id']}）"
        row["configured_model"] = workflow["model"] if workflow else None
        row["date"] = stamp(row["started_at"]).astimezone(ZONE).date().isoformat()
        runs.append(row)
    return runs, list(sources.values())


def read_outputs(run: dict, copilot: Path, upper: datetime) -> dict:
    result = dict(run)
    result["public_outputs"] = []
    result["source_note"] = ""
    if not run.get("session_id"):
        result["source_note"] = "运行记录没有 session_id；不能读取正文。"
        return result
    path = copilot / "session-state" / run["session_id"] / "events.jsonl"
    result["events_path"] = str(path)
    if not path.is_file():
        result["source_note"] = "源事件文件不存在或尚未创建；未编造报告内容。"
        return result
    lower = stamp(run["started_at"])
    if run.get("next_session_run"):
        upper = min(upper, stamp(run["next_session_run"]))
    with path.open("r", encoding="utf-8-sig") as stream:
        for number, line in enumerate(stream, 1):
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                if not line.endswith("\n"):
                    result["source_note"] = "源事件文件末行仍在写入，已保留其余已完成输出。"
                    break
                raise ValueError(f"Malformed source event: {path}:{number}") from None
            data = event.get("data", {})
            if event.get("agentId") or data.get("parentToolCallId"):
                continue
            if not lower <= stamp(event["timestamp"]) < upper:
                continue
            kind = event.get("type")
            text = data.get("content") if kind == "assistant.message" else (
                data.get("summary") if kind == "session.task_complete" else None
            )
            if isinstance(text, str) and text.strip():
                result["public_outputs"].append({
                    "event_id": event["id"], "timestamp": event["timestamp"],
                    "kind": kind, "model": data.get("model"),
                    "text": text, "sha256": digest(text),
                })
    return result


def report_parts(run: dict) -> tuple[dict | None, list[dict], list[dict]]:
    unique = {}
    for output in run.get("public_outputs", []):
        unique.setdefault(output["sha256"], output)
    candidates = [o for o in unique.values()
                  if o["kind"] == "session.task_complete" or len(o["text"].strip()) >= 500]
    if not candidates:
        return None, [], list(unique.values())
    primary = max(candidates, key=lambda o: (len(o["text"]), o["timestamp"]))
    supplements = [o for o in candidates if o is not primary
                   and o["kind"] == "assistant.message"]
    receipts = [o for o in unique.values() if o is not primary and o not in supplements]
    return primary, supplements, receipts


@lru_cache(maxsize=256)
def article_sections(text: str) -> tuple[tuple[int, int, str, int], ...]:
    tokens = MD.parse(text)
    headings = []
    heading_number = 0
    for position, token in enumerate(tokens):
        if token.type != "heading_open":
            continue
        heading_number += 1
        if token.level != 0:
            continue
        title = "".join(child.content for child in tokens[position + 1].children or []
                        if child.type in {"text", "code_inline", "image", "softbreak"}).strip()
        headings.append((token.map[0], int(token.tag[1]), title, heading_number))
    total = len(text.splitlines(keepends=True))
    title = headings[0][2] if headings else "全文（未识别到分篇标题）"
    context_heading = re.compile(
        r"(?:\d+[.)、]\s*)?(?:本期最值得看的.*|本期主线判断|本期新增台账条目|"
        r"Engineering Takeaways|参考资料|参考文献|来源|Sources?)", re.I
    )
    headings = [h for h in headings if not context_heading.fullmatch(h[2])]
    if len(headings) > 1 and sum(h[1] == 1 for h in headings) == 1:
        headings = [h for h in headings if h[1] != 1]
    if not headings:
        return ((0, total, title, 0),)
    level = min(h[1] for h in headings)
    top = [h for h in headings if h[1] == level]
    field_titles = {"这是什么？", "这是什么", "核心贡献", "为什么重要？", "机制拆解",
                    "设计取舍分析", "对 Agent / Coding Agent 架构的启发",
                    "对我的工程实践有什么启发？", "落到我的 Harness 上", "与已有工作的关系"}
    if all(h[2] in field_titles for h in top):
        return ((0, total, title, 0),)
    selected = []
    for position, heading in enumerate(top):
        start, _, name, number = heading
        label = re.sub(r"^(?:\d+[.)、]\s*|[一二三四五六七八九十]+[、.]\s*)", "", name).strip()
        end = top[position + 1][0] if position + 1 < len(top) else total
        children = [h for h in headings if start < h[0] < end and h[1] > level]
        if children and re.fullmatch(
            r"AI\s*/\s*Technology|Stocks?\s*&\s*Markets?|Microsoft\s*&\s*Career|Morgan Stanley Views",
            label, re.I
        ):
            child_level = min(h[1] for h in children)
            children = [h for h in children if h[1] == child_level]
            selected.append((start, children[0][2], number))
            selected.extend((h[0], h[2], h[3]) for h in children[1:])
        else:
            selected.append((start, name, number))
    if not selected:
        return ((0, total, title, 0),)
    # Keep introductions, section labels, references, and closing notes with the adjacent article.
    selected[0] = (0, selected[0][1], selected[0][2])
    return tuple((start, selected[i + 1][0] if i + 1 < len(selected) else total, name, number)
                 for i, (start, name, number) in enumerate(selected))


def output_articles(output: dict) -> list[dict]:
    lines = output["text"].splitlines(keepends=True)
    return [{
        "id": digest(json.dumps([output["event_id"], start], separators=(",", ":"))),
        "title": title, "fingerprint": digest("".join(lines[start:end])),
        "heading": heading, "event_id": output["event_id"],
    } for start, end, title, heading in article_sections(output["text"])]


def report_articles(run: dict) -> list[dict]:
    primary, supplements, _ = report_parts(run)
    return [item for output in ([primary, *supplements] if primary else [])
            for item in output_articles(output)]


def article_updated(item: dict, state: dict) -> bool:
    return state["status"] == "read" and state["read_fingerprint"] != item["fingerprint"]


def retained_favorites(run: dict, states: dict) -> list[dict]:
    active = {item["id"] for item in report_articles(run)}
    wanted = {key for key, state in states.items() if state.get("favorite", False) and key not in active}
    if not wanted:
        return []
    result = []
    for output in run.get("public_outputs", []):
        for item in output_articles(output):
            if item["id"] in wanted:
                result.append({**item, "retired": True})
                wanted.remove(item["id"])
        if not wanted:
            return result
    raise ValueError(f"Saved favorite article source is missing in report {run['id']}; kept read-only.")


def article_reading_progress(run: dict, states: dict) -> dict:
    result = {"articles": 0, "read_articles": 0, "unread_articles": 0, "updated_articles": 0,
              "favorite_articles": sum(state.get("favorite", False) for state in states.values())}
    for item in report_articles(run):
        state = states[item["id"]]
        kind = "updated" if article_updated(item, state) else state["status"]
        result[kind + "_articles"] += 1
        result["articles"] += 1
    return result


def article_progress(day: dict) -> dict:
    result = {"articles": 0, "read_articles": 0, "unread_articles": 0, "updated_articles": 0,
              "favorite_articles": 0}
    for run in day["runs"]:
        if report_parts(run)[0] is not None:
            progress = article_reading_progress(run, day["article_reading_states"][run["id"]])
            for key in result:
                result[key] += progress[key]
    return result


def set_reading_state(state: dict, fingerprint: str, read: bool, generated: str) -> None:
    state["status"] = "read" if read else "unread"
    if read:
        state.update(read_fingerprint=fingerprint, marked_read_at=generated)


def mark_report_articles(run: dict, states: dict, read: bool, generated: str) -> None:
    for item in report_articles(run):
        set_reading_state(states[item["id"]], item["fingerprint"], read, generated)


def merge_run(old: dict, new: dict) -> dict:
    merged = {**old, **new}
    outputs = {o["event_id"]: dict(o) for o in old.get("public_outputs", [])}
    for output in new["public_outputs"]:
        prior = outputs.get(output["event_id"], {})
        if prior.get("sha256") and prior["sha256"] != output["sha256"]:
            raise ValueError(f"Source event changed in place; old archive kept: {output['event_id']}")
        outputs[output["event_id"]] = {**prior, **output}
    merged["public_outputs"] = sorted(outputs.values(), key=lambda o: (o["timestamp"], o["event_id"]))
    return merged


def content_fingerprint(runs: list[dict]) -> str:
    delivered = set()
    for run in runs:
        primary, supplements, _ = report_parts(run)
        if primary:
            delivered.update((run["task_id"], output["sha256"])
                             for output in [primary, *supplements])
    return digest(json.dumps(sorted(delivered), separators=(",", ":")))


def report_updated(run: dict, state: dict) -> bool:
    return state["status"] == "read" and state["read_fingerprint"] != content_fingerprint([run])


def reading_progress(day: dict) -> dict:
    result = {"read_reports": 0, "unread_reports": 0, "updated_reports": 0}
    for run in day["runs"]:
        if report_parts(run)[0] is not None:
            state = day["report_reading_states"][run["id"]]
            key = "updated" if report_updated(run, state) else state["status"]
            result[key + "_reports"] += 1
    return result


def updated_since_read(day: dict) -> bool:
    return reading_progress(day)["updated_reports"] > 0


def reading_badge(day: dict) -> str:
    progress = reading_progress(day)
    total = sum(progress.values())
    label = f'已读 {progress["read_reports"]}/{total}' if total else "无已交付报告"
    kind = " unread" if progress["unread_reports"] else ""
    if progress["updated_reports"]:
        kind = " warn"
        label += f' · {progress["updated_reports"]} 份有更新'
    return f'<span class="badge{kind}">{label}</span>'


def report_reading_badge(run: dict, state: dict, progress: dict) -> str:
    if report_updated(run, state):
        return '<span class="badge warn">已读 · 有更新</span>'
    if state["status"] == "read":
        return '<span class="badge">已读</span>'
    if progress["read_articles"] or progress["updated_articles"]:
        return '<span class="badge unread">部分已读</span>'
    return '<span class="badge unread">未读</span>'


def validate_reading_state(state: object, path: Path) -> None:
    if not isinstance(state, dict) or state.get("status") not in {"read", "unread"}:
        raise ValueError(f"Unsupported reading state; kept read-only: {path}")
    fingerprint = state.get("read_fingerprint")
    if (fingerprint is not None or state["status"] == "read") and (
        not isinstance(fingerprint, str) or re.fullmatch(r"[0-9a-f]{64}", fingerprint) is None
    ):
        raise ValueError(f"Unsupported reading fingerprint; kept read-only: {path}")


def parse_archive(text: str, path: Path, kind: str) -> dict:
    soup = BeautifulSoup(text, "html.parser", parse_only=SoupStrainer("script", id="archive-data"))
    node = soup.find("script", id="archive-data", attrs={"type": "application/json"})
    if node is None:
        raise ValueError(f"Existing file is not a managed archive; not overwritten: {path}")
    value = json.loads(node.string or "")
    version = value.get("schema_version")
    if type(version) is not int or version not in {1, 2, 3, 4, 5, 6, SCHEMA_VERSION} or value.get("kind") != kind:
        raise ValueError(f"Unsupported archive schema or kind; kept read-only: {path}")
    if kind == "day":
        if value.get("date") != path.stem:
            raise ValueError(f"Archive date does not match its filename: {path}")
        ids = [r["id"] for r in value["runs"]]
        if len(ids) != len(set(ids)):
            raise ValueError(f"Duplicate archived run identities: {path}")
    entries = [value] if kind == "day" else list(value.get("days", {}).values()) if kind == "index" else []
    for entry in entries:
        state = entry.get("reading_state")
        if state is not None or version >= 2:
            validate_reading_state(state, path)
    if version >= 4 and kind == "day":
        states = value.get("report_reading_states")
        report_ids = {run["id"] for run in value["runs"] if report_parts(run)[0] is not None}
        if not isinstance(states, dict) or set(states) != report_ids:
            raise ValueError(f"Report reading identities do not match delivered reports; kept read-only: {path}")
        for state in states.values():
            validate_reading_state(state, path)
    if version >= 6 and kind == "day":
        articles = value.get("article_reading_states")
        if not isinstance(articles, dict) or set(articles) != report_ids:
            raise ValueError(f"Article reading report identities do not match; kept read-only: {path}")
        for run in value["runs"]:
            if run["id"] not in report_ids:
                continue
            states = articles[run["id"]]
            active = {item["id"] for item in report_articles(run)}
            if (not isinstance(states, dict) or not active <= states.keys()
                    or any(re.fullmatch(r"[0-9a-f]{64}", key) is None for key in states)):
                raise ValueError(f"Article reading identities do not match; kept read-only: {path}")
            for state in states.values():
                validate_reading_state(state, path)
                if type(state.get("favorite", False)) is not bool or not isinstance(state.get("favorite_reason", ""), str):
                    raise ValueError(f"Unsupported article favorite state; kept read-only: {path}")
            retained_favorites(run, states)
    if version >= 4 and kind == "index":
        for entry in entries:
            progress = [entry.get(key) for key in ("read_reports", "unread_reports", "updated_reports")]
            if any(type(number) is not int or number < 0 for number in progress) or sum(progress) != entry["reports"]:
                raise ValueError(f"Unsupported report reading counts; kept read-only: {path}")
            if version >= 6:
                progress = [entry.get(key) for key in ("read_articles", "unread_articles", "updated_articles")]
                total = entry.get("articles")
                if (type(total) is not int or total < 0
                        or any(type(number) is not int or number < 0 for number in progress)
                        or sum(progress) != total):
                    raise ValueError(f"Unsupported article reading counts; kept read-only: {path}")
            if version >= 7 and (type(entry.get("favorite_articles")) is not int or entry["favorite_articles"] < 0):
                raise ValueError(f"Unsupported favorite counts; kept read-only: {path}")
    if kind == "index" and (version >= 5 or "excluded_task_ids" in value):
        excluded = value.get("excluded_task_ids")
        if (not isinstance(excluded, list) or any(not isinstance(key, str) or not key for key in excluded)
                or len(set(excluded)) != len(excluded)):
            raise ValueError(f"Unsupported source exclusion ledger; kept read-only: {path}")
    return value


def read_archive_snapshot(path: Path, kind: str) -> tuple[dict | None, str | None]:
    if not path.exists():
        return None, None
    raw = path.read_bytes()
    return parse_archive(raw.decode("utf-8"), path, kind), hashlib.sha256(raw).hexdigest()


def read_archive(path: Path, kind: str) -> dict | None:
    return read_archive_snapshot(path, kind)[0]


def migrate_report_reading(day: dict, root: Path) -> dict:
    if day.get("schema_version", 0) >= 4:
        return {key: dict(state) for key, state in day["report_reading_states"].items()}
    if "report_reading_states" in day:
        raise ValueError("Legacy archive already has an unsupported report_reading_states field; kept read-only.")
    legacy_state = day.get("reading_state", {})
    baseline = day.get("runs", [])
    was_read = legacy_state.get("status") == "read"
    if was_read and legacy_state["read_fingerprint"] != content_fingerprint(baseline):
        for path in sorted((root / "_history" / day["date"]).glob("*.html")):
            # Historical copies have hash filenames but retain their original date identity.
            saved = parse_archive(path.read_text(encoding="utf-8"), root / f'{day["date"]}.html', "day")
            if content_fingerprint(saved["runs"]) == legacy_state["read_fingerprint"]:
                baseline = saved["runs"]
                break
        else:
            raise ValueError(
                f"Cannot recover the previously read version for {day['date']}; "
                "kept read-only instead of marking unseen updates read. Restore its _history baseline first."
            )
    read_runs = {run["id"]: run for run in baseline if was_read and report_parts(run)[0] is not None}
    return {
        run["id"]: {
            "status": "read" if run["id"] in read_runs else "unread",
            "read_fingerprint": content_fingerprint([read_runs[run["id"]]]) if run["id"] in read_runs else None,
            "marked_read_at": legacy_state.get("marked_read_at") if run["id"] in read_runs else None,
        }
        for run in day.get("runs", []) if report_parts(run)[0] is not None
    }


def migrate_article_reading(day: dict, root: Path, report_states: dict) -> dict:
    if day.get("schema_version", 0) >= 6:
        return {run_id: {key: dict(state) for key, state in states.items()}
                for run_id, states in day["article_reading_states"].items()}
    if "article_reading_states" in day:
        raise ValueError("Legacy archive already has an unsupported article_reading_states field; kept read-only.")
    result = {}
    for run in day.get("runs", []):
        if run["id"] not in report_states:
            continue
        state = report_states[run["id"]]
        baseline = run
        if report_updated(run, state):
            for path in sorted((root / "_history" / day["date"]).glob("*.html")):
                saved = parse_archive(path.read_text(encoding="utf-8"), root / f'{day["date"]}.html', "day")
                candidate = next((r for r in saved["runs"] if r["id"] == run["id"]), None)
                if candidate and content_fingerprint([candidate]) == state["read_fingerprint"]:
                    baseline = candidate
                    break
            else:
                raise ValueError(
                    f"Cannot recover the previously read report {run['id']} on {day['date']}; "
                    "restore its _history baseline before upgrading article states."
                )
        states = {
            item["id"]: {"status": "read", "read_fingerprint": item["fingerprint"],
                         "marked_read_at": state.get("marked_read_at")}
            for item in report_articles(baseline) if state["status"] == "read"
        }
        for item in report_articles(run):
            states.setdefault(item["id"], {"status": "unread", "read_fingerprint": None, "marked_read_at": None})
        result[run["id"]] = states
    return result


def validate_sources(day: dict, index: dict, path: Path) -> None:
    excluded = set(index.get("excluded_task_ids", []))
    if any(item["task_id"] in excluded for item in day["runs"] + day.get("pending_sources", [])):
        raise ValueError(
            f"Excluded source content found in {path}; not displayed, backed up, or recollected. "
            "Keep the authoritative index and clean the conflicting date file before continuing."
        )


def load_days(root: Path, index: dict) -> tuple[dict[str, dict], dict[str, Path], dict[str, str]]:
    days, locations, revisions = {}, {}, {}
    read_directory = root / READ_DIR
    if read_directory.is_symlink() or read_directory.is_junction():
        raise ValueError(f"Read folder must be a real archive subdirectory: {read_directory}")
    for directory in (root, read_directory):
        for path in sorted(directory.glob("*.html")):
            if not DATE_FILE.fullmatch(path.name):
                continue
            if path.stem in days:
                raise ValueError(
                    f"Duplicate date in unread and read folders; both files kept unchanged: {path.name}. "
                    "Move reports instead of copying them."
                )
            days[path.stem], revisions[path.stem] = read_archive_snapshot(path, "day")
            validate_sources(days[path.stem], index, path)
            locations[path.stem] = path
    for key in index.get("days", {}):
        if key not in days:
            raise FileNotFoundError(
                f"Indexed report is missing from both archive folders: {key}.html; "
                "not recreating an unread copy or discarding its history."
            )
    return days, locations, revisions


def file_link(text: str, run: dict, context: str | None = None) -> str:
    text = text.strip().rstrip("。，；、)]）")
    line_match = re.search(r":(\d+)(?:-(\d+))?$", text)
    line = line_match.group(1) if line_match else None
    clean = text[:line_match.start()] if line_match else text
    if re.match(r"^[A-Za-z]:\\", clean):
        mappings = {
            "d:\\spocore\\src\\": "https://onedrive.visualstudio.com/SharePoint%20Online/_git/SPO.Core",
            "d:\\code\\dbjobpackage\\": "https://onedrive.visualstudio.com/SPIN/_git/DBJobPackage",
        }
        for prefix, repo in mappings.items():
            if clean.lower().startswith(prefix):
                params = {"path": "/" + clean[len(prefix):].replace("\\", "/"), "_a": "contents"}
                if repo.endswith("SPO.Core"):
                    params["version"] = "GBmain"
                if line:
                    params.update(line=line, lineEnd=str(int(line) + 1), lineStyle="plain")
                return repo + "?" + urlencode(params, quote_via=quote)
        return PureWindowsPath(clean).as_uri() + (f"#L{line}" if line else "")
    if clean.lower().startswith(("files\\", "files/", "research\\", "research/")):
        return (COPILOT / "session-state" / run["session_id"] / Path(
            clean.replace("/", "\\")
        )).as_uri() + (f"#L{line}" if line else "")
    return context or source_url(run)


def render_markdown(text: str, run: dict, prefix: str) -> str:
    # Opaque citation IDs are linked back to the actual report, never guessed URLs.
    text = re.sub(
        r"\ue200(cite|filenavlist|navlist)\ue202([^\ue201]+)\ue201",
        lambda m: f"[原报告引用：{m.group(2)}]({source_url(run)})", text
    )
    soup = BeautifulSoup(MD.render(text), "html.parser")
    for image in soup.find_all("img"):
        link = soup.new_tag("a", href=image.get("src", source_url(run)))
        link.string = image.get("alt") or "原报告图片链接"
        image.replace_with(link)
    headings = {}
    for index, heading in enumerate(soup.find_all(re.compile(r"^h[1-6]$")), 1):
        slug = re.sub(r"[^\w\s-]", "", heading.get_text()).strip().lower().replace(" ", "-")
        headings.setdefault(slug, f"{prefix}-h{index}")
        heading["id"] = f"{prefix}-h{index}"
    context = None
    for node in list(soup.descendants):
        if getattr(node, "name", None) == "a":
            href = node.get("href", "")
            if href.startswith("#"):
                node["href"] = "#" + headings[href[1:]] if href[1:] in headings else source_url(run)
            elif re.match(r"^[A-Za-z]:\\", href):
                node["href"] = file_link(href, run)
            elif href and not urlparse(href).scheme:
                node["href"] = file_link(href, run, context)
                node["title"] = "相对路径：在原报告的来源上下文中查看"
            if "/pullrequest/" in href or re.match(r"https://github\.com/[^/]+/[^/]+", href):
                context = href
        if not isinstance(node, NavigableString) or node.find_parent(["a", "pre"]):
            continue
        value = str(node)
        if node.parent.name == "code" and (
            value.startswith(("https://", "http://")) or FILE_NAME.fullmatch(value)
            or re.match(r"^[A-Za-z]:\\", value)
        ):
            link = soup.new_tag("a", href=value if value.startswith(("https://", "http://"))
                                else file_link(value, run, context))
            link["title"] = "原始链接或来源上下文；相对文件名未猜测仓库路径"
            link.string = value
            node.replace_with(link)
            continue
        matches = list(SOURCE_REF.finditer(value))
        if not matches:
            continue
        position = 0
        for match in matches:
            node.insert_before(NavigableString(value[position:match.start()]))
            link = soup.new_tag("a", href=file_link(match.group(), run, context))
            link.string = match.group()
            link["title"] = "源码 / 本地文件引用；相对路径链接到原始来源上下文"
            node.insert_before(link)
            position = match.end()
        node.insert_before(NavigableString(value[position:]))
        node.extract()
    for anchor in soup.find_all("a"):
        href = anchor.get("href", "")
        if href.startswith(("http://", "https://")):
            anchor["target"] = "_blank"
            anchor["rel"] = "noopener noreferrer"
        if not href or (urlparse(href).scheme and urlparse(href).scheme.lower()
                        not in {"https", "http", "file", "ghapp", "mailto"}):
            anchor["href"] = source_url(run)
            anchor["title"] = "原链接不支持安全离线打开；请查看原报告"
    for table in soup.find_all("table"):
        table.wrap(soup.new_tag("div", attrs={"class": "table-wrap"}))
    return str(soup)


def render_excerpt(text: str, run: dict, prefix: str) -> str:
    soup = BeautifulSoup(render_markdown(text, run, prefix), "html.parser")
    heading = next((h for h in soup.find_all(re.compile(r"^h[1-6]$"))
                    if re.search(r"Executive Summary|执行摘要|主线判断|核心判断|本期最值得看", h.get_text(), re.I)), None)
    for link in soup.find_all("a", href=True):
        if link["href"].startswith("#"):
            link["href"] = "#run-" + run["id"]
    points, paragraphs = [], []
    nodes = heading.next_siblings if heading else soup.children
    for node in nodes:
        name = getattr(node, "name", None)
        if name and re.fullmatch(r"h[1-6]", name) and (points or paragraphs):
            break
        if name in {"ul", "ol"}:
            points.extend(item.decode_contents() for item in node.find_all("li", recursive=False))
        elif name == "div" and "table-wrap" in node.get("class", []):
            table = node.find("table")
            headers = [cell.get_text(" ", strip=True) for cell in table.find_all("th")]
            for row in table.find_all("tr"):
                cells = row.find_all("td", recursive=False)
                if not cells:
                    continue
                ranked = headers and re.fullmatch(r"排名|优先级|序号|rank|priority|#", headers[0], re.I)
                start = 1 if ranked and len(cells) > 1 and cells[0].get_text(strip=True).isdecimal() else 0
                point = f'<div class="summary-point-title">{cells[start].decode_contents()}</div>'
                for position in range(start + 1, len(cells)):
                    label = f'<span class="summary-label">{esc(headers[position])}：</span>' if position < len(headers) else ""
                    point += f'<p>{label}{cells[position].decode_contents()}</p>'
                points.append(point)
        elif name in {"p", "blockquote"}:
            value = node.get_text(" ", strip=True)
            if value and len(paragraphs) < 3 and not re.match(r"^(日期|时间窗口|截至|来源|主题轮转|\d{4}-\d{2}-\d{2})", value):
                paragraphs.append(str(node))
        if len(points) >= 3:
            break
    selected = (points or paragraphs)[:3]
    return '<ol class="summary-points">' + "".join(f"<li>{point}</li>" for point in selected) + "</ol>"


def article_anchor(run_id: str, article_id: str) -> str:
    return f"article-{run_id}-{article_id}"


def render_article_section(run: dict, item: dict, state: dict, content: str) -> str:
    updated = article_updated(item, state)
    label = "已读 · 有更新" if updated else "本篇已读" if state["status"] == "read" else "本篇未读"
    kind = " warn" if updated else " unread" if state["status"] == "unread" else ""
    favorite = '<span class="badge warn">★ 已收藏</span>' if state.get("favorite", False) else ""
    reason = ""
    if favorite and state.get("favorite_reason"):
        reason = ('<div class="favorite-reason"><strong>收藏原因</strong>'
                  + render_markdown(state["favorite_reason"], run, f'favorite-{run["id"]}-{item["id"]}') + "</div>")
    return (f'<section class="article-unit" id="{esc(article_anchor(run["id"], item["id"]))}" '
            f'data-article-id="{esc(item["id"])}">{content}'
            f'<div class="article-reading-status"><span class="badge{kind}">{label}</span>{favorite}</div>'
            f'{reason}</section>')


def render_article_body(output: dict, run: dict, prefix: str, states: dict,
                        article_ids: set[str] | None = None) -> str:
    items = output_articles(output)
    soup = BeautifulSoup(render_markdown(output["text"], run, prefix), "html.parser")
    boundaries = {f'{prefix}-h{item["heading"]}': i for i, item in enumerate(items) if i}
    fragments = [[] for _ in items]
    current = 0
    visited = {0}
    for node in soup.children:
        if getattr(node, "name", None) and node.get("id") in boundaries:
            current = boundaries[node["id"]]
            visited.add(current)
        fragments[current].append(str(node))
    if len(visited) != len(items):
        raise ValueError(f"Article boundaries do not match rendered headings in {run['id']}; not saving incomplete controls.")
    rendered = "".join(render_article_section(run, item, states[item["id"]], "".join(fragments[i]))
                       for i, item in enumerate(items) if article_ids is None or item["id"] in article_ids)
    if article_ids is not None:
        saved = BeautifulSoup(rendered, "html.parser")
        ids = {node["id"] for node in saved.find_all(id=True)}
        for link in saved.find_all("a", href=True):
            if link["href"].startswith("#") and link["href"][1:] not in ids:
                link["href"] = source_url(run)
                link["title"] = "旧版未展示章节：查看原始报告"
        rendered = str(saved)
    return rendered


def pending_sources(workflows: list[dict], runs: list[dict], day: date) -> list[dict]:
    result = []
    for workflow in workflows:
        if not workflow["enabled"]:
            continue
        matching = [r for r in runs if r["task_id"] == workflow["id"] and r["date"] == day.isoformat()]
        live = [r for r in matching if r["status"] in ACTIVE and not r.get("archived_at")
                and not r.get("taken_over_at")]
        scheduled = workflow["interval"] == "daily" or (
            workflow["interval"] == "weekly" and workflow["schedule_day"] == (day.weekday() + 1) % 7
        )
        due = scheduled and (workflow["schedule_hour"], workflow["schedule_minute"]) <= (11, 0)
        if live or (due and not matching and stamp(workflow["created_at"]).astimezone(ZONE).date() <= day):
            result.append({"task_id": workflow["id"], "name": workflow["name"],
                           "reason": "运行中，等待补收" if live else "计划于 11:00 或更早运行，尚无本日运行记录"})
    return result


def counts(runs: list[dict]) -> dict:
    return {
        "runs": len(runs),
        "reports": sum(report_parts(r)[0] is not None for r in runs),
        "failed": sum(r["status"] == "failed" for r in runs),
        "failed_with_output": sum(r["status"] == "failed" and report_parts(r)[0] is not None for r in runs),
        "missing_output": sum(report_parts(r)[0] is None for r in runs),
        "kinds": len({r["task_id"] for r in runs}),
    }


def page(title: str, subtitle: str, metrics: dict, body: str, payload: dict) -> str:
    metric_html = "".join(f'<div class="metric"><strong>{esc(value)}</strong><span>{esc(label)}</span></div>'
                          for label, value in metrics.items())
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="generator" content="Weekly AI Briefing Archive v{SCHEMA_VERSION}">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>{esc(title)}</title><style>{CSS}</style></head>
<body><header><div class="eyebrow">Weekly AI Briefing · Automation Archive</div>
<h1>{esc(title)}</h1><p>{subtitle}</p><div class="metrics">{metric_html}</div></header>
<main>{body}<footer>按 America/Los_Angeles 本地运行日期归档（自动适配夏令时）。
原始运行台账与源会话只读；报告及补录原文保存在本 HTML 内，可离线阅读。
网页不会自动加载外部图片、字体或脚本。<br>
这是历史报告整理，不是重新研究或事实核验；不同来源的观点和数字可能冲突，不合并为已验证结论。
市场内容不构成投资建议。工作相关报告仅供私人归档，分享前请检查敏感信息。<br>
<a href="index.html">全部日期与阅读标记说明</a>
· 本页更新：{esc(local_time(payload.get("generated_at")))}</footer></main>
<script type="application/json" id="archive-data">{data}</script></body></html>"""


def render_day(payload: dict) -> str:
    runs = sorted(payload["runs"], key=lambda r: (r["name"], r["started_at"], r["id"]))
    stat = counts(runs)
    toc, cards, articles, ledger = [], [], [], []
    seen_bodies = {}
    for run in runs:
        anchor = "run-" + run["id"]
        primary, supplements, _ = report_parts(run)
        label = run["name"]
        badge = "已保留正文" if primary else "未发现已交付正文"
        warning = run["status"] != "completed" or primary is None
        toc.append(f'<a href="#{anchor}">{esc(label)} · {esc(local_time(run["started_at"])[11:16])}</a>')
        reading = ""
        if primary:
            article_states = payload["article_reading_states"][run["id"]]
            progress = article_reading_progress(run, article_states)
            reading = report_reading_badge(run, payload["report_reading_states"][run["id"]], progress)
            reading += f'<span class="badge unread">文章已读 {progress["read_articles"]}/{progress["articles"]}</span>'
        if primary:
            cards.append(f'<div class="digest"><div class="digest-heading"><h3><a href="#{anchor}">{esc(label)}</a></h3>'
                         + reading + "</div>" + render_excerpt(primary["text"], run, anchor + "-excerpt")
                         + f'<a class="digest-more" href="#{anchor}">阅读全文与补充更正 →</a></div>')
        article = (
            f'<article id="{anchor}" data-run-id="{esc(run["id"])}"><h2>{esc(label)}'
            f'<span class="badge{" warn" if warning else ""}">{badge}</span>{reading}</h2>'
            f'<p class="meta">启动：{esc(local_time(run["started_at"]))} · '
            f'台账状态：{esc(run["status"])} · 完成：{esc(local_time(run.get("completed_at")))}<br>'
            f'<a href="{esc(source_url(run))}">打开源会话</a>'
        )
        if run.get("events_path"):
            article += f'<a href="{esc(Path(run["events_path"]).as_uri())}">本地源记录</a>'
        article += "</p>"
        notes = []
        if run["status"] == "failed":
            notes.append("原运行台账标记为 failed；即使随后找到了交付正文，也保留该失败状态，不改写历史。")
        elif run["status"] not in TERMINAL | ACTIVE:
            notes.append(f"未知运行状态 {run['status']}，原值只读保留；不会据此恢复源任务。")
        if run.get("archived_at"):
            notes.append("原运行已归档；此处仅保留报告，不重新激活该运行。")
        if run.get("taken_over_at"):
            notes.append("原运行曾被接管；此处仅归档公开输出。")
        if run.get("source_note"):
            notes.append(run["source_note"])
        if supplements:
            notes.append("本次报告含补充交付或更正，请结合正文后的补录一起阅读。")
        if notes:
            article += '<div class="notice">' + "<br>".join(esc(n) for n in notes) + "</div>"
        if primary:
            fingerprint = digest(json.dumps([primary["sha256"]] + [o["sha256"] for o in supplements]))
            if fingerprint in seen_bodies:
                prior_run, prior_items = seen_bodies[fingerprint]
                article += f'<p>正文与本日 <a href="#run-{esc(prior_run)}">此前交付</a> 完全相同；'
                article += "正文只展示一次，独立运行记录和原文快照仍保留。</p>"
                article += '<div class="report-body">'
                for item, prior_item in zip(report_articles(run), prior_items, strict=True):
                    link = (f'<p><a href="#{esc(article_anchor(prior_run, prior_item["id"]))}">'
                            f'{esc(item["title"])} · 阅读对应正文</a></p>')
                    article += render_article_section(run, item, article_states[item["id"]], link)
                article += "</div>"
            else:
                seen_bodies[fingerprint] = (run["id"], report_articles(run))
                article += f'<p class="meta">正文输出：{esc(local_time(primary["timestamp"]))}'
                article += f' · 原文 {len(primary["text"]):,} 字符</p><div class="report-body">'
                article += render_article_body(primary, run, anchor, article_states) + "</div>"
                for i, extra in enumerate(supplements, 1):
                    article += f'<details open><summary>补充交付 / 更正 {i} · {esc(local_time(extra["timestamp"]))}</summary>'
                    article += f'<div class="report-body">{render_article_body(extra, run, anchor + f"-extra{i}", article_states)}</div></details>'
            retained = retained_favorites(run, article_states)
            if retained:
                article += '<details open class="retained-favorites"><summary>旧版收藏 · 不计入当前报告阅读进度</summary>'
                wanted = {item["id"] for item in retained}
                for output in run["public_outputs"]:
                    if any(item["event_id"] == output["event_id"] for item in retained):
                        prefix = anchor + "-saved-" + digest(output["event_id"])[:16]
                        article += '<div class="report-body">' + render_article_body(
                            output, run, prefix, article_states, wanted
                        ) + "</div>"
                article += "</details>"
        else:
            article += "<p>没有找到该主会话的报告终稿；不会把子代理研究笔记或进度消息冒充报告。</p>"
        if run.get("error_message"):
            article += '<details><summary>原始失败原因</summary><pre>' + esc(run["error_message"]) + "</pre></details>"
        articles.append(article + "</article>")
        ledger.append(f'<tr><td><a href="#{anchor}">{esc(label)}</a></td>'
                      f'<td>{esc(local_time(run["started_at"]))}</td><td>{esc(run["status"])}</td>'
                      f'<td>{"有正文" if primary else "无终稿"}</td>'
                      f'<td><a href="{esc(source_url(run))}">{esc(run["id"])}</a></td></tr>')
    pending = payload.get("pending_sources", [])
    body = '<nav class="card toc"><a href="index.html">← 日期总索引</a>' + "".join(toc) + "</nav>"
    if updated_since_read(payload):
        body += '<div class="notice"><strong>部分已读报告有新内容。</strong>其他报告的阅读状态保持不变。'
        body += '请在本地阅读器中逐篇阅读新文章或更正；全部读完后，该份报告自动恢复为已读。'
        body += '直接打开的静态 HTML 只显示状态，操作方法见<a href="index.html">总索引</a>。</div>'
    if pending:
        body += '<div class="notice"><strong>尚待补收：</strong>' + "；".join(
            esc(p["name"] + "：" + p["reason"]) for p in pending
        ) + "。当前页面不是最终完整版本。</div>"
    body += '<section class="card" id="daily-overview"><h2>本日速览</h2><p class="muted">每篇节选至多三个原文要点，保留原文强调与来源链接；完整内容及更正见下方。不是二次核验或跨报告一致结论。</p>'
    body += '<div class="digest-list">' + "".join(cards) + "</div></section>" if cards else "<p>本日没有可读取的已交付正文。</p></section>"
    body += "".join(articles)
    body += '<section class="card" id="coverage"><h2>运行覆盖与来源</h2><div class="table-wrap"><table>'
    body += "<thead><tr><th>Automation</th><th>启动时间</th><th>原状态</th><th>归档结果</th><th>运行 ID</th></tr></thead>"
    body += "<tbody>" + "".join(ledger) + "</tbody></table></div></section>"
    return page(payload["date"], 'Automation 每日报告合辑 · <a href="index.html">查看所有日期</a> · '
                + reading_badge(payload),
                {"报告 / 交付正文": stat["reports"], "运行记录": stat["runs"], "原失败记录": stat["failed"],
                 "报告类别": stat["kinds"]}, body, payload)


def render_index(payload: dict, days: dict[str, dict]) -> str:
    all_runs = [run for day in days.values() for run in day["runs"]]
    stat = counts(all_runs)
    rows, missing = [], []
    cursor = date.fromisoformat(payload["coverage_end"])
    first = date.fromisoformat(payload["coverage_start"])
    while cursor >= first:
        key = cursor.isoformat()
        if key in days:
            day = days[key]
            item = counts(day["runs"])
            categories = " · ".join(sorted({r["name"] for r in day["runs"]}))
            pending = len(day.get("pending_sources", []))
            relative_path = payload["days"][key]["relative_path"]
            href = quote(relative_path.replace("\\", "/"), safe="/-_.")
            rows.append(f'<tr><td><a href="{href}"><strong>{key}</strong></a></td>'
                        f'<td>{reading_badge(day)}</td>'
                        f'<td>{item["reports"]}</td><td>{item["runs"]}</td><td>{item["failed"]}</td>'
                        f'<td>{esc(categories)}</td><td>{"待补收 " + str(pending) if pending else "已收集"}</td></tr>')
        else:
            missing.append(key)
            rows.append(f'<tr class="empty"><td>{key}</td><td colspan="6">未发现 Automation 运行记录；不创建空白报告。</td></tr>')
        cursor -= timedelta(days=1)
    body = '<section class="card"><h2>按日期阅读</h2><p>同一天的市场简报、AI 学习、PR 评审与数据库健康报告集中在一个日期文件中。'
    body += "不同模型的判断分别保留；完全相同的正文折叠，补录和更正不会丢失。</p>"
    body += f'<div class="notice">原始台账中有 {stat["failed"]} 次失败运行，其中 {stat["failed_with_output"]} 次仍找到了已交付正文并予以保留。'
    body += f'另有 {stat["missing_output"]} 次没有终稿，已记录原因；没有将它们记作成功报告。</div>'
    body += '<div class="table-wrap"><table><thead><tr><th>日期</th><th>阅读状态</th><th>报告</th><th>运行</th><th>失败</th><th>类别</th><th>收集状态</th></tr></thead><tbody>'
    body += "".join(rows) + "</tbody></table></div></section>"
    body += '<section class="card" id="reading-guide"><h2>快速标记已读</h2>'
    body += f'<p><strong>网页阅读器：</strong>在资源管理器中双击 <a href="{quote("打开报告阅读器.cmd")}">打开报告阅读器.cmd</a>，'
    body += '左侧按“日期 → Automation → 文章”三级展开。点击 Automation 名称打开原报告摘要及文章目录，'
    body += '点击文章打开正文；目录中的已读/未读按钮与正文末尾的标记相同，不会因打开页面自动已读。'
    body += '网页只通过本机服务操作现有文件，不上传报告；关闭启动窗口即可停止服务。</p>'
    body += '<p><strong>逐篇记录：</strong>每份 Automation 显示“文章已读几篇 / 共几篇”；所有文章、独立总结及补充更正都读完后，'
    body += '该份报告自动标为已读。取消任一篇的已读会让所属报告回到未读；其他文章及报告不变。'
    body += '状态按稳定运行 ID、原始输出 ID 和分篇位置写入本 HTML 的 article_reading_states，随 OneDrive 同步，不依赖浏览器缓存。'
    body += '普通小节保留在所属文章内，导读和参考资料跟随相邻文章；没有分篇标题的短报告作为一篇全文。'
    body += '逐篇标记不会移动当天文件；直接双击静态 HTML 只能查看保存后的状态。</p>'
    body += '<p><strong>文章收藏：</strong>点击“收藏”可填写可选原因，已收藏后可编辑原因或取消收藏。'
    body += '“收藏”筛选只列出含收藏的日期、报告及文章，收藏与阅读状态相互独立。'
    body += '收藏及原因也保存在现有 article_reading_states 中，取消收藏保留之前的原因，方便重新收藏。'
    body += '报告改版后不再属于当前正文的收藏仍可阅读原始版本，标注为“旧版收藏”，不计入当前报告的已读进度。</p>'
    body += f'<p><strong>整天整理：</strong><a href="{quote(READ_DIR)}/">已读目录</a>仍保留。'
    body += '阅读器的“整天已读并归档”是明确的批量操作：确认当天已保存的所有报告并移动文件；'
    body += '“整天设为未读”则将所有文章及报告设为未读并移回根目录。逐篇读完不会自动移动文件。</p>'
    launcher = quote("刷新阅读状态.cmd")
    body += f'<p>索引是静态页面：移动后可双击根目录的 <a href="{launcher}">刷新阅读状态.cmd</a>，'
    body += '然后重新加载本页；也可等待下一次 Automation 刷新。手动移入已读目录视为整天批量已读，'
    body += '移回根目录视为整天批量未读；未移动文件的普通刷新不改变逐篇状态。</p>'
    body += '<p><strong>更快的操作：</strong>把读完的 HTML 直接拖到同一个 CMD 文件上，'
    body += '会将所选文件移入已读目录并立即刷新索引；已经在已读目录中的文件会确认当前版本。</p>'
    body += '<p>已读报告补收新正文或更正后，只有该份显示<strong>“已读 · 有更新”</strong>，'
    body += '不会冒出未读副本。新交付的报告独立从未读开始，即使当天文件已在已读目录。'
    body += '已保存文章的阅读记录不丢失，新输出和补充更正从未读开始；普通刷新和每日 Automation 不会替你确认新内容。</p>'
    body += '<p class="muted">源报告的 completed / failed 状态与阅读状态相互独立。'
    body += '同一天若根目录和已读目录都有文件，程序会停止并保留两份，不会自动删除或覆盖。</p></section>'
    body += '<section class="card" id="coverage"><h2>归档口径</h2><p>范围：'
    body += f'{esc(payload["coverage_start"])} — {esc(payload["coverage_end"])}。'
    body += f"有记录的日期 {len(days)} 天；无运行记录的日期 {len(missing)} 天。</p>"
    body += '<p>每个日期文件都内嵌原始公开输出和稳定运行 ID。重复执行只补充新输出和状态，不删除旧报告。'
    body += '发生更新时，旧 HTML 会保存在 <a href="_history/">_history</a>；未知归档版本或非本程序文件不会被覆盖。</p>'
    body += '<p>归档格式 v7 保存逐文章阅读状态、收藏原因、报告汇总与来源排除规则，并保护网页版本校验与并发写入；旧 v1/v2/v3/v4/v5/v6 文件会在备份后无损升级，'
    body += '旧程序遇到新格式会只读退出，不会覆盖新的阅读状态。'
    body += '<a href="_tools/archive_reports.py">归档程序</a>不修改源 Automation 的台账。</p>'
    body += '<p>用户已排除的 Automation 来源 ID 保存在本索引的 excluded_task_ids 中。'
    body += '每日收集与待补收检查均跳过这些来源；不要删除索引或使用旧备份替换它来绕过排除规则。</p>'
    body += '<p>旧版已经读完的报告，其已确认版本内的文章继承已读；已读后有更新的报告会从 _history 找回已读版本，'
    body += '只继承该版本已有文章的已读记录。找不到基线时停止并报告，'
    body += '不把更新冒充已读。跨机器使用前应等 OneDrive 同步完成，避免两台机器同时改写同一份文件；本机锁不是跨机器锁。</p>'
    body += '<p>每日归档在本机太平洋时间 11:00 启动。同期仍在生成的报告由原生会话续跑补收，次日再次扫描最近一个月，补齐延迟交付。</p></section>'
    progress = [reading_progress(day) for day in days.values()]
    return page("Automation 报告归档", f'{esc(payload["coverage_start"])} — {esc(payload["coverage_end"])} · 私人离线阅读库',
                {"日期文件": len(days), "报告 / 交付正文": stat["reports"], "运行记录": stat["runs"],
                 "当前版本已读": sum(item["read_reports"] for item in progress),
                 "未读报告": sum(item["unread_reports"] for item in progress),
                 "已读后有更新": sum(item["updated_reports"] for item in progress)}, body, payload)


def render_read_navigation() -> str:
    data = json.dumps({"schema_version": SCHEMA_VERSION, "kind": "navigation"})
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; base-uri 'none'; form-action 'none'">
<meta http-equiv="refresh" content="0;url=../index.html"><title>已读报告 · 返回总索引</title></head>
<body><p><a href="../index.html">打开报告总索引与阅读标记说明</a></p>
<p>此导航页让报告移入已读文件夹后仍能返回总索引；请保留它。</p>
<script type="application/json" id="archive-data">{data}</script></body></html>"""


def backup_html(path: Path, root: Path) -> Path:
    old = path.read_bytes()
    backup = root / "_history" / path.stem / (digest(old.decode("utf-8"))[:16] + ".html")
    backup.parent.mkdir(parents=True, exist_ok=True)
    if not backup.exists():
        backup.write_bytes(old)
    return backup


def write_html(path: Path, content: str, root: Path) -> bool:
    if path.exists():
        if path.read_bytes() == content.encode("utf-8"):
            return False
        backup_html(path, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".pending")
    if temporary.exists():
        raise FileExistsError(f"Previous pending write needs inspection: {temporary}")
    with temporary.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(content)
    temporary.replace(path)
    return True


class ArchiveBusyError(RuntimeError):
    pass


class ArchiveChangedError(RuntimeError):
    pass


@contextmanager
def writer_lock(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".archive.lock").open("a+b") as stream:
        stream.seek(0)
        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            if error.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                raise ArchiveBusyError("另一个归档操作正在写入，请稍后重试；没有强制解锁或覆盖文件。") from error
            raise
        try:
            yield
        finally:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def archive(first: date, last: date, root: Path = ROOT, copilot: Path = COPILOT,
            *, refresh_only: bool = False, mark_read: tuple[Path, ...] = (),
            mark_unread: tuple[Path, ...] = (), expected_revisions: dict[str, str] | None = None,
            report_change: tuple[str, str, bool] | None = None,
            article_change: tuple[str, str, str, bool] | None = None,
            favorite_change: tuple[str, str, str, bool, str] | None = None) -> dict:
    with writer_lock(root.resolve()):
        return _archive(first, last, root, copilot, refresh_only=refresh_only,
                        mark_read=mark_read, mark_unread=mark_unread, expected_revisions=expected_revisions,
                        report_change=report_change, article_change=article_change, favorite_change=favorite_change)


def _archive(first: date, last: date, root: Path, copilot: Path, *, refresh_only: bool,
             mark_read: tuple[Path, ...], mark_unread: tuple[Path, ...],
             expected_revisions: dict[str, str] | None, report_change: tuple[str, str, bool] | None,
             article_change: tuple[str, str, str, bool] | None,
             favorite_change: tuple[str, str, str, bool, str] | None = None) -> dict:
    if first > last:
        raise ValueError("--from-date must not be later than --to-date")
    now = datetime.now(timezone.utc)
    today = now.astimezone(ZONE).date()
    if last > today:
        raise ValueError("Cannot archive a future date")
    root = root.resolve()
    if sum(bool(action) for action in (mark_read, mark_unread, report_change, article_change, favorite_change)) > 1:
        raise ValueError("Choose one reading or favorite action.")
    refresh_only = refresh_only or bool(mark_read or mark_unread or report_change or article_change or favorite_change)
    index_data, index_revision = read_archive_snapshot(root / "index.html", "index")
    existing_index = index_data or {}
    days, locations, revisions = load_days(root, existing_index)
    if index_data is None and (days or (root / "_history").exists()):
        raise FileNotFoundError("Authoritative archive index is missing; not initializing replacement reading or exclusion history.")
    original_locations = dict(locations)
    navigation_path = root / READ_DIR / "index.html"
    _, navigation_revision = read_archive_snapshot(navigation_path, "navigation")
    observed = {root / "index.html": index_revision, navigation_path: navigation_revision}
    observed.update((locations[key], revision) for key, revision in revisions.items())
    legacy_paths = [path for key, path in locations.items() if days[key]["schema_version"] < SCHEMA_VERSION]
    if existing_index and existing_index["schema_version"] < SCHEMA_VERSION:
        legacy_paths.append(root / "index.html")
    acknowledged, unread_selected = set(), set()
    by_location = {path.resolve(): key for key, path in locations.items()}
    for selected_files, folder, selected_keys in (
        (mark_read, root / READ_DIR, acknowledged), (mark_unread, root, unread_selected)
    ):
        for selected in selected_files:
            selected = selected if selected.is_absolute() else root / selected
            key = by_location.get(selected.resolve(strict=True))
            if key is None:
                raise ValueError(f"Only managed date reports in this archive can be marked: {selected}")
            selected_keys.add(key)
            locations[key] = folder / f"{key}.html"
    selected_dates = acknowledged | unread_selected
    selection = favorite_change or article_change or report_change
    if selection is not None:
        key, run_id = selection[:2]
        read = favorite_change[3] if favorite_change is not None else selection[-1]
        if not isinstance(key, str) or not isinstance(run_id, str) or type(read) is not bool:
            raise ValueError("Report reading action requires a date, run ID, and boolean read state.")
        if favorite_change is not None and not isinstance(favorite_change[4], str):
            raise ValueError("收藏原因必须是文本；没有更改收藏或阅读状态。")
        selected_run = next((run for run in days.get(key, {}).get("runs", []) if run["id"] == run_id), None)
        if selected_run is None or report_parts(selected_run)[0] is None:
            raise ValueError("指定报告不存在或尚无已交付正文，不能标记已读。")
        if expected_revisions is None:
            raise ValueError("Individual report actions require the displayed document revision.")
        if any(day["schema_version"] != SCHEMA_VERSION for day in days.values()):
            raise ValueError("请先刷新索引，完成旧版阅读状态升级后再逐篇标记。")
        if any((locations[key].parent == root / READ_DIR) != (day["reading_state"]["status"] == "read")
               for key, day in days.items()):
            raise ArchiveChangedError("检测到手动移动的文件。请先刷新目录，再逐份标记；本次未改变阅读状态。")
        selected_dates.add(key)
    if expected_revisions is not None:
        if set(expected_revisions) != selected_dates:
            raise ValueError("Expected revisions must match exactly the selected dates.")
        for key, expected in expected_revisions.items():
            if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
                raise ValueError("Invalid report revision.")
            if revisions[key] != expected:
                raise ArchiveChangedError("报告在你打开后发生了变化。请重新加载正文，再保存阅读或收藏操作；本次未写入状态。")
    if article_change is not None or favorite_change is not None:
        key, run_id = selection[:2]
        items = report_articles(selected_run)
        if favorite_change is not None:
            states = days[key]["article_reading_states"][run_id]
            items += retained_favorites(selected_run, states)
        if not isinstance(selection[2], str) or selection[2] not in {item["id"] for item in items}:
            raise ValueError("指定文章不属于当前报告或保留的收藏，不能修改其状态。")
    grouped = defaultdict(list)
    if refresh_only:
        if not days:
            raise ValueError("No archived reports to refresh; run the normal archive command first.")
        first = date.fromisoformat(existing_index.get("coverage_start", min(days)))
        last = date.fromisoformat(existing_index.get("coverage_end", max(days)))
        pending = days.get(today.isoformat(), {}).get("pending_sources", [])
    else:
        runs, workflows = read_ledger(
            copilot, first, last, excluded_task_ids=tuple(existing_index.get("excluded_task_ids", []))
        )
        upper = datetime.combine(last + timedelta(days=1), time.min, ZONE).astimezone(timezone.utc)
        for run in runs:
            grouped[run["date"]].append(read_outputs(run, copilot, upper))
        pending = pending_sources(workflows, runs, today) if first <= today <= last else []
        if last == today:
            grouped.setdefault(today.isoformat(), [])
    changed = []
    changed_days = set()
    generated = now.isoformat()
    for key in sorted(days.keys() | grouped.keys()):
        old = days.get(key, {})
        location = locations.setdefault(key, root / f"{key}.html")
        # The legacy day state tracks folder/bulk actions, not individual reading progress.
        previous_reading = old.get("reading_state", {})
        status = "read" if location.parent == root / READ_DIR else "unread"
        reading = {**previous_reading, "status": status}
        report_states = migrate_report_reading(old, root)
        article_states = migrate_article_reading(old, root, report_states)
        if previous_reading.get("status") != status or key in acknowledged | unread_selected:
            for run in old.get("runs", []):
                if run["id"] not in report_states:
                    continue
                set_reading_state(report_states[run["id"]], content_fingerprint([run]), status == "read", generated)
                mark_report_articles(run, article_states[run["id"]], status == "read", generated)
        if status == "read" and (previous_reading.get("status") != "read" or key in acknowledged):
            # A move acknowledges the saved snapshot, not reports fetched afterwards.
            reading.update(read_fingerprint=content_fingerprint(old["runs"]), marked_read_at=generated)
        reading.setdefault("read_fingerprint", None)
        reading.setdefault("marked_read_at", None)
        if report_change is not None and report_change[0] == key:
            _, run_id, read = report_change
            run = next(run for run in old["runs"] if run["id"] == run_id)
            set_reading_state(report_states[run_id], content_fingerprint([run]), read, generated)
            mark_report_articles(run, article_states[run_id], read, generated)
        if article_change is not None and article_change[0] == key:
            _, run_id, article_id, read = article_change
            run = next(run for run in old["runs"] if run["id"] == run_id)
            item = next(item for item in report_articles(run) if item["id"] == article_id)
            set_reading_state(article_states[run_id][article_id], item["fingerprint"], read, generated)
            if not read:
                report_states[run_id]["status"] = "unread"
        if favorite_change is not None and favorite_change[0] == key:
            _, run_id, article_id, favorite, reason = favorite_change
            article_states[run_id][article_id].update(favorite=favorite, favorite_reason=reason)
        merged_runs = {r["id"]: r for r in old.get("runs", [])}
        for run in grouped.get(key, []):
            merged_runs[run["id"]] = merge_run(merged_runs.get(run["id"], {}), run)
        for run in merged_runs.values():
            if report_parts(run)[0] is not None:
                state = report_states.setdefault(run["id"], {"status": "unread", "read_fingerprint": None, "marked_read_at": None})
                states = article_states.setdefault(run["id"], {})
                for item in report_articles(run):
                    states.setdefault(item["id"], {"status": "unread", "read_fingerprint": None, "marked_read_at": None})
                progress = article_reading_progress(run, states)
                if progress["read_articles"] == progress["articles"]:
                    if state["status"] != "read" or report_updated(run, state):
                        set_reading_state(state, content_fingerprint([run]), True, generated)
                elif state["status"] == "read" and not report_updated(run, state):
                    state["status"] = "unread"
        updated = {
            **old, "schema_version": SCHEMA_VERSION, "kind": "day", "date": key,
            "timezone": str(ZONE), "runs": sorted(merged_runs.values(), key=lambda r: (r["started_at"], r["id"])),
            "reading_state": reading,
            "report_reading_states": report_states,
            "article_reading_states": article_states,
        }
        if key in grouped:
            updated["pending_sources"] = pending if key == today.isoformat() else []
        if {k: v for k, v in updated.items() if k != "generated_at"} != {
            k: v for k, v in old.items() if k != "generated_at"
        }:
            updated["generated_at"] = generated
            changed_days.add(key)
        updated.setdefault("generated_at", generated)
        days[key] = updated
    index = {
        **existing_index, "schema_version": SCHEMA_VERSION, "kind": "index",
        "excluded_task_ids": existing_index.get("excluded_task_ids", []),
        "coverage_start": min(first.isoformat(), existing_index.get("coverage_start", first.isoformat())),
        "coverage_end": max(last.isoformat(), existing_index.get("coverage_end", last.isoformat())),
        "days": {key: {**existing_index.get("days", {}).get(key, {}),
                       **counts(day["runs"]), **reading_progress(day), **article_progress(day),
                       "generated_at": day["generated_at"],
                       "pending_sources": day.get("pending_sources", []),
                       "relative_path": str(locations[key].relative_to(root)),
                       "reading_state": {**existing_index.get("days", {}).get(key, {}).get("reading_state", {}),
                                         **day["reading_state"]},
                       "updated_since_read": updated_since_read(day)}
                 for key, day in sorted(days.items())},
    }
    if {k: v for k, v in index.items() if k != "generated_at"} != {
        k: v for k, v in existing_index.items() if k != "generated_at"
    }:
        index["generated_at"] = generated
    index.setdefault("generated_at", generated)
    rendered = {locations[key]: render_day(day) for key, day in days.items()
                if not refresh_only or key in changed_days}
    rendered[root / "index.html"] = render_index(index, days)
    rendered[navigation_path] = render_read_navigation()
    for key in locations:
        present = [path for path in (root / f"{key}.html", root / READ_DIR / f"{key}.html")
                   if path.exists()]
        expected = [original_locations[key]] if key in original_locations else []
        if present != expected:
            raise ArchiveChangedError(f"Report moved while refreshing; retry without recreating a duplicate: {key}")
    for path in rendered:
        if path.with_name(path.name + ".pending").exists():
            raise FileExistsError(f"Previous pending write needs inspection: {path}.pending")
    for path, expected in observed.items():
        current = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
        if current != expected:
            raise ArchiveChangedError(f"文件在整理期间发生变化，未覆盖它，请重新加载后重试：{path}")
    (root / "_history").mkdir(parents=True, exist_ok=True)
    (root / READ_DIR).mkdir(parents=True, exist_ok=True)
    for path in legacy_paths:
        backup_html(path, root)
    for key in sorted(acknowledged | unread_selected):
        original = original_locations[key]
        if original != locations[key]:
            backup_html(original, root)
            original.rename(locations[key])
    for path, content in rendered.items():
        if write_html(path, content, root):
            changed.append(str(path.relative_to(root)))
    all_runs = [run for day in days.values() for run in day["runs"]]
    progress = {key: reading_progress(day) for key, day in days.items()}
    missing_dates = []
    cursor = date.fromisoformat(index["coverage_start"])
    while cursor <= date.fromisoformat(index["coverage_end"]):
        if cursor.isoformat() not in days:
            missing_dates.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return {
        "index": str(root / "index.html"), "today_file": str(locations[today.isoformat()]) if today.isoformat() in days else None,
        "coverage_start": index["coverage_start"], "coverage_end": index["coverage_end"],
        "date_files": len(days), **counts(all_runs), "changed_files": changed,
        "read_directory": str(root / READ_DIR), "refresh_only": refresh_only,
        "excluded_task_ids": index["excluded_task_ids"],
        "read_days": sum(item["read_reports"] > 0 and not item["unread_reports"] and not item["updated_reports"]
                         for item in progress.values()),
        "unread_days": sum(item["unread_reports"] > 0 or item["updated_reports"] > 0 for item in progress.values()),
        **{field: sum(item[field] for item in progress.values())
           for field in ("read_reports", "unread_reports", "updated_reports")},
        **{field: sum(item[field] for item in index["days"].values())
           for field in ("articles", "read_articles", "unread_articles", "updated_articles", "favorite_articles")},
        "updated_read_dates": [key for key, day in sorted(days.items()) if updated_since_read(day)],
        "marked_read_dates": sorted(acknowledged),
        "marked_unread_dates": sorted(unread_selected),
        "marked_report": {"date": report_change[0], "run_id": report_change[1], "read": report_change[2]}
                         if report_change is not None else None,
        "marked_article": {"date": article_change[0], "run_id": article_change[1],
                           "article_id": article_change[2], "read": article_change[3]}
                          if article_change is not None else None,
        "marked_favorite": {"date": favorite_change[0], "run_id": favorite_change[1],
                            "article_id": favorite_change[2], "favorite": favorite_change[3]}
                           if favorite_change is not None else None,
        "no_run_dates": missing_dates, "pending_sources": pending,
        "today_reports": counts(days[today.isoformat()]["runs"])["reports"] if today.isoformat() in days else 0,
        "source_warnings": [{"run_id": r["id"], "note": r["source_note"]}
                            for r in all_runs if r.get("source_note")],
    }


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-date", type=date.fromisoformat)
    parser.add_argument("--to-date", type=date.fromisoformat)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--refresh-index", action="store_true")
    modes.add_argument("--mark-read", type=Path, nargs="+", metavar="FILE")
    modes.add_argument("--mark-unread", type=Path, nargs="+", metavar="FILE")
    args = parser.parse_args()
    if (args.refresh_index or args.mark_read or args.mark_unread) and (args.from_date or args.to_date):
        parser.error("Reading-state commands use saved reports, not a date-limited source scan.")
    last = args.to_date or datetime.now(ZONE).date()
    result = archive(args.from_date or month_before(last), last, refresh_only=args.refresh_index,
                     mark_read=tuple(args.mark_read or ()), mark_unread=tuple(args.mark_unread or ()))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
