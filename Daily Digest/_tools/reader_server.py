"""Private, loopback-only report reader. Run with --open-browser on Windows."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import secrets
import sys
import webbrowser
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

import archive_reports as archive


ASSETS = Path(__file__).with_name("reader")
LOGGER = logging.getLogger("report-reader")
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")

def whole_report(run: dict) -> bool:
    return re.fullmatch(r"AI Market Briefing\s*-\s*(GPT|Opus)", run["name"], re.I) is not None


def reading_units(payload: dict) -> dict:
    progress = archive.article_progress(payload)
    for run in payload["runs"]:
        if not whole_report(run) or run["id"] not in payload["report_reading_states"]:
            continue
        old = archive.article_reading_progress(run, payload["article_reading_states"][run["id"]])
        for field in ("articles", "read_articles", "unread_articles", "updated_articles"):
            progress[field] -= old[field]
        state = payload["report_reading_states"][run["id"]]
        status = "updated" if archive.report_updated(run, state) else state["status"]
        progress["articles"] += 1
        progress[status + "_articles"] += 1
    return progress


def day_state(progress: dict) -> str:
    if not progress["reports"]:
        return "empty"
    if progress["read_reports"] == progress["reports"]:
        return "read"
    return "partial" if progress["read_reports"] or progress["updated_reports"] or progress["read_articles"] else "unread"


class ReaderStore:
    def __init__(self, root: Path):
        self.root = root.resolve(strict=True)

    def index(self) -> dict:
        value = archive.read_archive(self.root / "index.html", "index")
        if value is None:
            raise FileNotFoundError("归档总索引不存在，请先运行每日归档程序。")
        if value["schema_version"] != archive.SCHEMA_VERSION:
            raise ValueError("请先运行 archive_reports.py --refresh-index，升级文章阅读及收藏格式后再打开阅读器。")
        return value

    def report_path(self, key: str, index: dict | None = None) -> Path:
        if not DATE.fullmatch(key):
            raise ValueError("无效的报告日期。")
        date.fromisoformat(key)
        if key not in (self.index() if index is None else index)["days"]:
            raise FileNotFoundError("总索引中没有这个日期。")
        candidates = [path for path in (self.root / f"{key}.html", self.root / archive.READ_DIR / f"{key}.html")
                      if path.is_file()]
        if len(candidates) != 1:
            raise archive.ArchiveChangedError("报告文件缺失或两处重名，请检查根目录和已读目录；没有自动重建或删除文件。")
        path = candidates[0]
        if path.resolve().parent not in {self.root, self.root / archive.READ_DIR}:
            raise ValueError("报告必须位于实际归档目录内，不能通过链接访问外部文件。")
        return path

    def catalog(self) -> dict:
        index = self.index()
        entries = []
        for key, stored in sorted(index["days"].items(), reverse=True):
            path = self.report_path(key, index)
            payload = archive.read_archive(path, "day")
            archive.validate_sources(payload, index, path)
            units = reading_units(payload)
            mismatch = str(path.relative_to(self.root)) != stored.get("relative_path")
            entries.append({
                "date": key, "reports": stored["reports"], "runs": stored["runs"],
                "failed": stored["failed"], "state": day_state(stored),
                "read_reports": stored["read_reports"], "unread_reports": stored["unread_reports"],
                "updated_reports": stored["updated_reports"], "updated": stored["updated_reports"] > 0,
                **units,
                "archived": path.parent.name == archive.READ_DIR,
                "needs_refresh": mismatch, "pending": len(stored.get("pending_sources", [])),
                "generated_at": stored["generated_at"],
                "reports_info": [{
                    "id": run["id"], "name": run["name"],
                    "read": state["status"] == "read" and not archive.report_updated(run, state),
                    "favorites": archive.article_reading_progress(
                        run, payload["article_reading_states"][run["id"]])["favorite_articles"],
                } for run in payload["runs"]
                    if (state := payload["report_reading_states"].get(run["id"])) is not None],
            })
        return {
            "entries": entries, "root": str(self.root),
            "read_folder": archive.READ_DIR, "generated_at": index["generated_at"],
            "needs_refresh": any(item["needs_refresh"] for item in entries),
        }

    def document(self, key: str) -> tuple[bytes, dict]:
        index = self.index()
        path = self.report_path(key, index)
        raw = path.read_bytes()
        payload = archive.parse_archive(raw.decode("utf-8"), path, "day")
        archive.validate_sources(payload, index, path)
        if payload["schema_version"] != archive.SCHEMA_VERSION:
            raise ValueError("该日期仍为旧版状态，请先刷新索引完成逐文章升级。")
        archived = path.parent.name == archive.READ_DIR
        mismatch = archived != (payload["reading_state"]["status"] == "read")
        progress = {**archive.counts(payload["runs"]), **archive.reading_progress(payload),
                    **reading_units(payload)}
        context = {
            "date": key, "revision": hashlib.sha256(raw).hexdigest(),
            "source_revision": archive.digest(json.dumps(payload["runs"], ensure_ascii=False, sort_keys=True)),
            **progress, "state": day_state(progress), "updated": progress["updated_reports"] > 0,
            "needs_refresh": mismatch, "archived": archived,
            "generated_at": payload["generated_at"], "path": str(path.relative_to(self.root)),
            "items": [{
                "id": run["id"], "name": run["name"], "started_at": run["started_at"],
                "whole_report": whole_report(run),
                "state": payload["report_reading_states"][run["id"]]["status"],
                "updated": archive.report_updated(run, payload["report_reading_states"][run["id"]]),
                "progress": archive.article_reading_progress(run, payload["article_reading_states"][run["id"]]),
                "articles": [{
                    "id": item["id"], "title": item["title"], "anchor": archive.article_anchor(run["id"], item["id"]),
                    "state": payload["article_reading_states"][run["id"]][item["id"]]["status"],
                    "updated": archive.article_updated(item, payload["article_reading_states"][run["id"]][item["id"]]),
                    "favorite": payload["article_reading_states"][run["id"]][item["id"]].get("favorite", False),
                    "favorite_reason": payload["article_reading_states"][run["id"]][item["id"]].get("favorite_reason", ""),
                    "retired": item.get("retired", False),
                } for item in archive.report_articles(run) + archive.retained_favorites(
                    run, payload["article_reading_states"][run["id"]]
                )],
            } for run in payload["runs"] if run["id"] in payload["report_reading_states"]],
        }
        return raw, context

    def change(self, key: str, revision: str, *, read: bool, run_id: str | None = None,
               article_id: str | None = None) -> dict:
        path = self.report_path(key)
        today = datetime.now(archive.ZONE).date()
        action = {"article_change": (key, run_id, article_id, read)} if article_id is not None else {
            "report_change": (key, run_id, read)
        } if run_id is not None else {
            "mark_read": (path,) if read else (), "mark_unread": () if read else (path,),
        }
        archive.archive(
            today, today, self.root, refresh_only=True,
            expected_revisions={key: revision}, **action,
        )
        catalog = self.catalog()
        _, context = self.document(key)
        catalog["document"] = {field: context[field] for field in ("date", "revision", "source_revision")}
        return catalog

    def refresh(self) -> dict:
        today = datetime.now(archive.ZONE).date()
        archive.archive(today, today, self.root, refresh_only=True)
        return self.catalog()

    def favorite(self, key: str, revision: str, run_id: str, article_id: str, favorite: bool, reason: str) -> dict:
        today = datetime.now(archive.ZONE).date()
        archive.archive(
            today, today, self.root, refresh_only=True, expected_revisions={key: revision},
            favorite_change=(key, run_id, article_id, favorite, reason),
        )
        return self.catalog()


def render_document(raw: bytes, context: dict) -> bytes:
    soup = BeautifulSoup(raw.decode("utf-8"), "html.parser")
    for tag in soup.find_all("script"):
        tag.decompose()
    if soup.head is None or soup.body is None:
        raise ValueError("报告缺少有效的 HTML head/body，未显示不完整内容。")
    info = soup.new_tag("script", attrs={"id": "reader-context", "type": "application/json"})
    info.string = json.dumps(context, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    soup.head.insert(0, info)
    soup.body["id"] = "reader-top"
    for report in context["items"]:
        if report["whole_report"]:
            heading = soup.find(id=f'run-{report["id"]}').find("h2")
            for label in heading.select(".badge"):
                if label.get_text().startswith("文章已读"):
                    label.string = "整篇已读" if report["state"] == "read" and not report["updated"] else "整篇未读"
    for anchor in soup.find_all("a"):
        if anchor.get("href") == "index.html":
            anchor["href"] = "#reader-top"
            anchor.string = "日期列表在左侧"
        if anchor.get("href", "").startswith(("https://", "http://")):
            anchor["target"] = "_blank"
            anchor["rel"] = "noopener noreferrer"
    return str(soup).encode("utf-8")


class ReaderServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, root: Path, port: int = 0):
        self.store = ReaderStore(root)
        self.store.index()
        self.token = secrets.token_urlsafe(32)
        super().__init__(("127.0.0.1", port), ReaderHandler)
        self.origin = f"http://127.0.0.1:{self.server_port}"
        self.prefix = f"/{self.token}/"
        self.url = self.origin + self.prefix


class ReaderHandler(BaseHTTPRequestHandler):
    server: ReaderServer
    server_version = "LocalReportReader"

    def log_message(self, format, *args):
        LOGGER.debug(format, *args)

    def log_error(self, format, *args):
        LOGGER.error(format, *args)

    def respond(self, status: int, data: bytes | dict, content_type: str = "application/json; charset=utf-8",
                *, report: bool = False, download: str | None = None):
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8") if isinstance(data, dict) else data
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        if report:
            self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; "
                             "base-uri 'none'; form-action 'none'; sandbox allow-same-origin allow-popups allow-popups-to-escape-sandbox")
        else:
            self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'self'; style-src 'self'; "
                             "connect-src 'self'; frame-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'none'")
        if download:
            self.send_header("Content-Disposition", f'attachment; filename="{download}"')
        self.end_headers()
        self.wfile.write(raw)

    def error(self, status: int, code: str, message: str):
        self.respond(status, {"error": code, "message": message})

    def route(self) -> str | None:
        path = urlsplit(self.path).path
        if self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}":
            self.error(403, "forbidden_host", "不允许该 Host 访问本机阅读器。")
            return None
        if not path.startswith(self.server.prefix):
            self.error(404, "not_found", "此阅读器地址无效，请使用启动窗口显示的完整地址。")
            return None
        return path[len(self.server.prefix):]

    def do_GET(self):
        route = self.route()
        if route is None:
            return
        try:
            assets = {"": ("index.html", "text/html; charset=utf-8"),
                      "reader.js": ("reader.js", "text/javascript; charset=utf-8"),
                      "reader.css": ("reader.css", "text/css; charset=utf-8")}
            if route in assets:
                filename, mime = assets[route]
                self.respond(200, (ASSETS / filename).read_bytes(), mime)
            elif route == "api/catalog":
                self.respond(200, self.server.store.catalog())
            elif re.fullmatch(r"(documents|download)/\d{4}-\d{2}-\d{2}", route):
                action, key = route.split("/")
                raw, context = self.server.store.document(key)
                if action == "download":
                    self.respond(200, raw, "text/html; charset=utf-8", download=f"{key}.html")
                else:
                    self.respond(200, render_document(raw, context), "text/html; charset=utf-8", report=True)
            else:
                self.error(404, "not_found", "没有此页面或接口。")
        except FileNotFoundError as error:
            self.error(404, "missing_report", str(error))
        except (ValueError, archive.ArchiveChangedError) as error:
            self.error(409, "archive_inconsistent", str(error))
        except PermissionError as error:
            self.error(403, "file_permission", str(error))

    def do_POST(self):
        route = self.route()
        if route is None:
            return
        if self.headers.get("Origin") != self.server.origin or self.headers.get("X-Reader-Action") != "1":
            self.error(403, "forbidden_origin", "拒绝非阅读器页面发起的文件操作。")
            return
        if self.headers.get_content_type() != "application/json":
            self.error(415, "content_type", "操作请求必须是 JSON。")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= (65536 if route == "api/article-favorite" else 4096):
                raise ValueError("操作请求为空或过大。")
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError("操作请求必须是对象。")
            if route == "api/refresh" and data == {}:
                result = self.server.store.refresh()
            elif route == "api/article-favorite":
                if (set(data) != {"date", "revision", "run_id", "article_id", "favorite", "reason"}
                        or any(not isinstance(data[key], str) for key in ("date", "run_id", "article_id", "reason"))
                        or type(data["favorite"]) is not bool):
                    raise ValueError("收藏操作必须指定日期、版本、报告/文章 ID、布尔收藏状态和文本原因。")
                result = self.server.store.favorite(
                    data["date"], data["revision"], data["run_id"], data["article_id"],
                    data["favorite"], data["reason"],
                )
            elif route in {"api/report-reading", "api/article-reading"}:
                fields = {"date", "revision", "run_id", "read"}
                if route == "api/article-reading":
                    fields.add("article_id")
                if (set(data) != fields or
                        not isinstance(data["date"], str) or not isinstance(data["run_id"], str) or
                        type(data["read"]) is not bool or
                        (route == "api/article-reading" and not isinstance(data["article_id"], str))):
                    raise ValueError("阅读标记必须指定日期、正在阅读的版本、报告/文章 ID 和布尔已读状态。")
                result = self.server.store.change(
                    data["date"], data["revision"], read=data["read"], run_id=data["run_id"],
                    article_id=data.get("article_id"),
                )
            elif route in {"api/archive", "api/unarchive"}:
                if set(data) != {"date", "revision"} or not isinstance(data["date"], str):
                    raise ValueError("操作必须指定日期及正在阅读的版本。")
                result = self.server.store.change(data["date"], data["revision"], read=route == "api/archive")
            else:
                self.error(404, "not_found", "没有此文件操作。")
                return
            self.respond(200, result)
        except archive.ArchiveBusyError as error:
            self.error(423, "archive_busy", str(error))
        except (archive.ArchiveChangedError, FileExistsError) as error:
            self.error(409, "report_changed", str(error))
        except FileNotFoundError as error:
            self.error(404, "missing_report", str(error))
        except (ValueError, UnicodeDecodeError) as error:
            self.error(400, "invalid_request", str(error))
        except PermissionError as error:
            self.error(403, "file_permission", str(error))

    def do_OPTIONS(self):
        self.error(403, "cross_origin_disabled", "阅读器不接受跨站请求。")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive-root", type=Path, default=archive.ROOT)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--open-browser", action="store_true")
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error("Port must be between 0 and 65535.")
    for name in ("index.html", "reader.js", "reader.css"):
        if not (ASSETS / name).is_file():
            parser.error(f"Reader asset missing: {ASSETS / name}")
    logging.basicConfig(level=logging.WARNING)
    with ReaderServer(args.archive_root, args.port) as server:
        print(json.dumps({"url": server.url, "root": str(server.store.root), "loopback_only": True},
                         ensure_ascii=False), flush=True)
        print("Keep this window open while reading. Close it or press Ctrl+C to stop.", flush=True)
        if args.open_browser and not webbrowser.open(server.url):
            print("Browser did not open automatically; open the printed URL manually.", file=sys.stderr, flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("Reader stopped.", flush=True)


if __name__ == "__main__":
    main()
