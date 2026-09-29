"use strict";

const base = new URL("./", window.location.href);
const $ = (id) => document.getElementById(id);
const state = {
  catalog: null, selected: null, document: null, filter: "all", writing: false, loading: false, scrollTop: 0,
  expandedDate: null, expandedReports: new Set(), view: { runId: null, articleId: null }, favorite: null
};
const weekdays = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];
const runTime = new Intl.DateTimeFormat("zh-CN", { timeZone: "America/Los_Angeles", hour: "2-digit", minute: "2-digit" });
// Article/report reading marks are applied locally first and saved in order in the background.
const writes = { queue: [], running: false, revisions: new Map(), idle: [] };

const isRead = (item) => item.state === "read" && !item.updated;
const readLast = (entries, read) => entries
  .map((entry, index) => [entry, index])
  .sort(([a, i], [b, j]) => Number(read(a)) - Number(read(b)) || i - j)
  .map(([entry]) => entry);

function badge(item, doc = document) {
  const node = doc.createElement("span");
  const kind = item.updated ? "updated" : item.state;
  node.className = doc === document ? `state-badge state-${kind}` :
    `badge${kind === "updated" ? " warn" : kind === "read" ? "" : " unread"}`;
  node.textContent = item.updated ? "有更新" :
    { read: "已读", partial: "部分已读", unread: "未读", empty: "无报告" }[item.state];
  return node;
}

function notice(text, kind = "success", reload = false) {
  $("message").hidden = !text;
  $("message").className = `message ${kind}`;
  $("message-text").textContent = text;
  $("reload-report").hidden = !reload;
}

function reportError(error, saved = false) {
  const changed = error.code === "report_changed";
  if (saved && state.document) state.document.needs_refresh = true;
  notice(saved ? `阅读标记已保存，但页面状态同步失败：${error.message} 请重新加载当前报告。` :
    error.message || "操作失败。请确认本地阅读器窗口仍在运行。", "error", changed || saved);
  $("reader-status").textContent = saved ? "阅读标记已保存；请重新加载当前报告同步显示。" :
    "操作未完成；原报告和阅读状态没有被静默替换。";
}

async function api(route, data) {
  const response = await fetch(new URL(route, base), data === undefined ? { cache: "no-store" } : {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Reader-Action": "1" },
    body: JSON.stringify(data),
    cache: "no-store"
  });
  const body = await (response.headers.get("Content-Type")?.startsWith("text/html") ?
    response.text() : response.json());
  if (!response.ok) {
    const error = new Error(body.message || `请求失败：HTTP ${response.status}`);
    error.code = body.error;
    throw error;
  }
  return body;
}

const KINDS = { learning: /Learning/i, briefing: /Briefing/i };
state.kinds = new Set(Object.keys(KINDS));

function kindVisible(name) {
  const kinds = Object.keys(KINDS).filter((kind) => KINDS[kind].test(name));
  return !kinds.length || kinds.some((kind) => state.kinds.has(kind));
}

const kindsFiltered = () => state.kinds.size < Object.keys(KINDS).length;

function visibleReports(item) {
  return (item.reports_info || []).filter((report) => kindVisible(report.name));
}

function dayRead(item) {
  const reports = visibleReports(item);
  return reports.length > 0 && reports.every((report) => report.read);
}

function matchesFilter(item, filter) {
  const reports = visibleReports(item);
  if (kindsFiltered() && !reports.length) return false;
  if (filter === "all") return true;
  if (filter === "unread") return reports.some((report) => !report.read);
  if (filter === "archived") return item.archived;
  if (filter === "favorites") return reports.some((report) => report.favorites > 0);
  return item.state === filter;
}

function reportVisible(report) {
  if (!kindVisible(report.name)) return false;
  if (state.filter === "favorites") return report.progress.favorite_articles > 0;
  return true;
}

function filteredEntries() {
  return readLast((state.catalog?.entries || []).filter((item) => matchesFilter(item, state.filter)), dayRead);
}

function disclosure(label, expanded, groupId, action) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "tree-toggle";
  button.textContent = expanded ? "▾" : "▸";
  button.dataset.navKey = `toggle-${groupId}`;
  button.setAttribute("aria-label", `${expanded ? "收起" : "展开"} ${label}`);
  button.setAttribute("aria-expanded", String(expanded));
  button.setAttribute("aria-controls", groupId);
  button.addEventListener("click", action);
  return button;
}

function wholeReport(report) {
  return report.whole_report;
}

function readingIcon(report, item = null) {
  const target = item || { ...report, title: report.name };
  const read = isRead(target);
  const button = document.createElement("button");
  button.type = "button";
  button.className = `tree-read-check${target.updated ? " updated" : ""}`;
  button.dataset.writeAction = "reading";
  button.dataset.navKey = `read-${report.id}-${item?.id || "report"}`;
  button.setAttribute("role", "checkbox");
  button.setAttribute("aria-checked", String(read));
  button.textContent = read ? "✓" : target.updated ? "•" : "";
  button.title = read ? "已读 · 点击设为未读" : target.updated ? "有更新 · 点击标记已读" : "点击标记已读";
  button.setAttribute("aria-label", `${target.title}：${button.title}`);
  button.addEventListener("click", (event) => {
    event.stopPropagation();
    changeReadingState(!read, report, item);
  });
  return button;
}

function favoriteButton(doc, report, item) {
  const button = doc.createElement("button");
  button.type = "button";
  button.className = "article-favorite-button";
  button.dataset.writeAction = "favorite";
  button.dataset.navKey = `favorite-${report.id}-${item.id}`;
  button.textContent = item.favorite ? "★ 已收藏 · 编辑原因" : "☆ 收藏";
  button.setAttribute("aria-pressed", String(item.favorite));
  button.setAttribute("aria-label", `${item.title}：${item.favorite ? "编辑收藏原因" : "收藏文章"}`);
  button.title = item.favorite_reason || button.textContent;
  button.addEventListener("click", () => openFavorite(report, item));
  return button;
}

function reportBranch(report) {
  const branch = document.createElement("li");
  branch.dataset.runId = report.id;
  if (isRead(report)) branch.className = "is-read";
  const row = document.createElement("div");
  row.className = "tree-row";
  const expanded = state.expandedReports.has(report.id);
  const groupId = `articles-${report.id}`;
  const toggle = disclosure(report.name, expanded, groupId, () => {
    if (expanded) state.expandedReports.delete(report.id);
    else state.expandedReports.add(report.id);
    renderSidebar();
    setControls();
  });
  const button = document.createElement("button");
  button.type = "button";
  button.className = "report-item";
  button.dataset.runId = report.id;
  button.dataset.navKey = `report-${report.id}`;
  button.title = `${report.name} · ${runTime.format(new Date(report.started_at))} · ${wholeReport(report) ? "阅读全文" : "打开总结"}`;
  if (state.view.runId === report.id && !state.view.articleId) button.setAttribute("aria-current", "page");
  const name = document.createElement("strong");
  name.textContent = report.name;
  const progress = document.createElement("div");
  progress.className = "report-progress";
  const text = document.createElement("span");
  text.textContent = `${wholeReport(report) ? `${report.state === "read" && !report.updated ? 1 : 0}/1 篇` :
    `${report.progress.read_articles}/${report.progress.articles} 篇`} · ${runTime.format(new Date(report.started_at))}`;
  const status = report.state === "unread" && report.progress.read_articles ? "partial" : report.state;
  progress.append(text, badge({ ...report, state: status }));
  button.append(name, progress);
  button.addEventListener("click", () => openView(report.id));
  row.append(toggle, button);
  if (wholeReport(report)) row.append(readingIcon(report));
  branch.append(row);
  if (wholeReport(report) && state.filter !== "favorites") {
    toggle.hidden = true;
    return branch;
  }
  const articles = document.createElement("ul");
  articles.id = groupId;
  articles.className = "tree-articles";
  articles.hidden = !expanded;
  articles.setAttribute("aria-label", `${report.name} 的文章`);
  const ordered = readLast(report.articles.map((item, position) => ({ item, position })),
    ({ item }) => item.retired || isRead(item));
  ordered.forEach(({ item, position }) => {
    if (state.filter === "favorites" && !item.favorite) return;
    const leaf = document.createElement("li");
    leaf.className = `article-row${item.retired || isRead(item) ? " is-read" : ""}`;
    leaf.dataset.articleId = item.id;
    const article = document.createElement("button");
    article.type = "button";
    article.className = "article-item";
    article.dataset.navKey = `article-${report.id}-${item.id}`;
    article.title = item.title;
    if (state.view.runId === report.id && state.view.articleId === item.id) article.setAttribute("aria-current", "page");
    const ordinal = document.createElement("span");
    ordinal.className = "article-ordinal";
    ordinal.textContent = item.retired ? "旧版" : `${position + 1}.`;
    article.append(ordinal, document.createTextNode(item.title.replace(/^\d+[.)、]\s*/, "")));
    article.addEventListener("click", () => openView(report.id, item.id));
    const actions = document.createElement("div");
    actions.className = "tree-article-actions";
    if (item.retired) {
      const retired = document.createElement("span");
      retired.className = "retired-label";
      retired.textContent = "旧版收藏";
      actions.append(retired);
    } else {
      leaf.append(readingIcon(report, item));
    }
    const favorite = favoriteButton(document, report, item);
    favorite.className = "tree-favorite-button";
    favorite.textContent = item.favorite ? "★ 已收藏" : "☆ 收藏";
    actions.append(favorite);
    leaf.prepend(article);
    leaf.append(actions);
    articles.append(leaf);
  });
  branch.append(articles);
  return branch;
}

function renderSidebar() {
  const entries = state.catalog?.entries || [];
  document.querySelectorAll("[data-count]").forEach((node) => {
    node.textContent = entries.filter((item) => matchesFilter(item, node.dataset.count)).length;
  });
  document.querySelectorAll("[data-filter]").forEach((node) => {
    node.setAttribute("aria-pressed", String(node.dataset.filter === state.filter));
  });
  const list = $("date-list");
  const scrollTop = list.scrollTop;
  const focused = list.contains(document.activeElement) ? document.activeElement.dataset.navKey : null;
  list.replaceChildren();
  let month = "";
  let dates;
  let readSection = false;
  for (const item of filteredEntries()) {
    const key = item.date.slice(0, 7);
    const read = dayRead(item);
    if (key !== month || read !== readSection) {
      month = key;
      readSection = read;
      const heading = document.createElement("h2");
      heading.className = `month-heading${read ? " read-section-heading" : ""}`;
      heading.textContent = `${read ? "已读 · " : ""}${key.slice(0, 4)} 年 ${Number(key.slice(5))} 月`;
      list.append(heading);
      dates = document.createElement("ul");
      dates.className = "tree-dates";
      dates.setAttribute("aria-label", heading.textContent);
      list.append(dates);
    }
    const branch = document.createElement("li");
    branch.className = "tree-day";
    branch.dataset.date = item.date;
    const row = document.createElement("div");
    row.className = "tree-row";
    const expanded = state.expandedDate === item.date;
    const groupId = `reports-${item.date}`;
    row.append(disclosure(item.date, expanded, groupId, () => {
      if (expanded) {
        state.expandedDate = null;
        renderSidebar();
        setControls();
      } else if (state.document?.date === item.date) {
        state.expandedDate = item.date;
        renderSidebar();
        setControls();
      } else {
        selectDate(item.date, 0, { runId: null, articleId: null }, false);
      }
    }));
    const button = document.createElement("button");
    button.type = "button";
    button.className = "date-item";
    button.dataset.date = item.date;
    button.dataset.navKey = `date-${item.date}`;
    button.setAttribute("aria-label", `${item.date}，已读 ${item.read_reports}/${item.reports} 份，文章已读 ${item.read_articles}/${item.articles} 篇，${item.updated_reports} 份有更新`);
    if (item.date === state.selected) button.setAttribute("aria-current", "date");
    const top = document.createElement("div");
    top.className = "date-top";
    const title = document.createElement("strong");
    title.textContent = `${item.date.slice(5, 7)} 月 ${item.date.slice(8)} 日`;
    const weekday = document.createElement("span");
    weekday.className = "weekday";
    weekday.textContent = weekdays[new Date(`${item.date}T12:00:00Z`).getUTCDay()];
    title.append(weekday);
    top.append(title, badge(item));
    const bottom = document.createElement("div");
    bottom.className = "date-bottom";
    const count = document.createElement("span");
    count.textContent = `已读 ${item.read_reports}/${item.reports} 份 · ${item.read_articles}/${item.articles} 篇`;
    const extra = document.createElement("span");
    extra.textContent = item.needs_refresh ? "目录待同步" : item.pending ? `${item.pending} 项待补收` :
      item.archived ? "已归档" : item.failed ? `${item.failed} 次原运行失败` : "完整合辑";
    bottom.append(count, extra);
    button.append(top, bottom);
    button.addEventListener("click", () => {
      notice("");
      selectDate(item.date);
    });
    row.append(button);
    branch.append(row);
    const reports = document.createElement("ul");
    reports.id = groupId;
    reports.className = "tree-reports";
    reports.hidden = !expanded;
    reports.setAttribute("aria-label", `${item.date} 的 Automation`);
    if (expanded) {
      if (state.document?.date === item.date) {
        const sorted = readLast([...state.document.items].sort((a, b) =>
          a.name.localeCompare(b.name) || a.started_at.localeCompare(b.started_at) || a.id.localeCompare(b.id)), isRead);
        for (const report of sorted) {
          if (reportVisible(report)) reports.append(reportBranch(report));
        }
      }
      if (!reports.childElementCount) {
        const empty = document.createElement("li");
        empty.className = "list-empty";
        empty.textContent = state.loading ? "正在加载报告目录…" :
          state.document ? state.filter === "favorites" ? "没有收藏的文章。" : "尚无已交付报告。" : "点击日期重试加载目录。";
        reports.append(empty);
      }
    }
    branch.append(reports);
    dates.append(branch);
  }
  if (!list.childElementCount) {
    const empty = document.createElement("p");
    empty.className = "list-empty";
    empty.textContent = "没有匹配的日期。可切换筛选或清空搜索。";
    list.append(empty);
  }
  list.scrollTop = scrollTop;
  if (focused) list.querySelector(`[data-nav-key="${CSS.escape(focused)}"]`)?.focus({ preventScroll: true });
}

function setControls() {
  document.body.classList.toggle("writing", state.writing);
  $("report-frame").setAttribute("aria-busy", String(state.writing));
  const ready = state.document && !state.loading && !state.writing &&
    !state.document.needs_refresh && !state.catalog?.needs_refresh;
  const archived = state.document?.archived;
  const allRead = state.document?.state === "read";
  $("archive").disabled = !ready || !state.document.reports || (archived && allRead);
  $("archive").textContent = archived ?
    (allRead ? "整天已归档" : "整天确认已读") : "整天已读并归档";
  $("unarchive").disabled = !ready ||
    (!archived && !state.document?.read_articles && !state.document?.updated_reports);
  $("refresh").disabled = state.writing;
  $("download").setAttribute("aria-disabled", String(!ready));
  document.querySelectorAll(".date-list button, [data-filter], [data-kind]").forEach((button) => {
    button.disabled = state.writing;
  });
  for (const doc of [document, $("report-frame").contentDocument]) {
    doc?.querySelectorAll("[data-write-action]").forEach((button) => { button.disabled = !ready; });
  }
  for (const id of ["favorite-save", "favorite-remove", "favorite-cancel", "favorite-reason"]) {
    $(id).disabled = state.writing;
  }
}

function applyCatalog(catalog) {
  state.catalog = catalog;
  $("archive-root").textContent = catalog.root;
  $("archive-root").title = catalog.root;
  renderSidebar();
  if (catalog.needs_refresh) {
    notice("检测到手动移动的文件。点击“刷新”即可同步目录和阅读标记；不会重新生成未读副本。", "warning");
  }
}

function updateAddress() {
  if (!state.selected) return;
  const query = new URLSearchParams({ date: state.selected });
  if (state.view.runId) query.set("report", state.view.runId);
  if (state.view.articleId) query.set("article", state.view.articleId);
  if (state.filter !== "all") query.set("filter", state.filter);
  if (kindsFiltered()) query.set("kinds", [...state.kinds].join(","));
  history.replaceState(null, "", `${base.pathname}?${query}`);
}

function selectDate(key, scrollTop = 0, view = { runId: null, articleId: null }, closeSidebar = true) {
  if (state.writing) return;
  state.selected = key;
  state.expandedDate = key;
  state.view = { ...view };
  if (view.runId) state.expandedReports.add(view.runId);
  state.document = null;
  state.loading = true;
  state.scrollTop = scrollTop;
  $("selected-date").textContent = key;
  $("selected-meta").textContent = "正在加载已保存的报告…";
  $("report-frame").hidden = true;
  $("document-placeholder").hidden = false;
  $("placeholder-title").textContent = "正在打开报告";
  $("placeholder-text").textContent = "正文来自本机归档文件，选择日期不会自动标记已读。";
  $("download").href = new URL(`download/${key}`, base).href;
  $("report-frame").src = new URL(`documents/${key}`, base).href;
  updateAddress();
  if (closeSidebar) document.body.classList.remove("sidebar-open");
  renderSidebar();
  setControls();
}

const CONTROL_STYLE = ".article-reading-status{display:none}";

function installReportControls(doc, context) {
  if (!doc.getElementById("reader-control-style")) {
    const style = doc.createElement("style");
    style.id = "reader-control-style";
    style.textContent = CONTROL_STYLE;
    doc.head.append(style);
  }
  for (const report of context.items) {
    for (const item of report.articles) {
      const article = doc.getElementById(item.anchor);
      if (!article) throw new Error(`文章正文缺少对应位置：${item.title}。没有启用该文章的收藏操作。`);
      const group = doc.createElement("div");
      group.className = "article-controls";
      group.setAttribute("role", "group");
      group.setAttribute("aria-label", `${item.title} 收藏`);
      group.append(favoriteButton(doc, report, item));
      if (item.retired) {
        const hint = doc.createElement("span");
        hint.className = "muted";
        hint.textContent = "旧版收藏保留原文，不计入当前报告的阅读进度";
        group.append(hint);
      }
      const previous = article.querySelector(":scope > .article-controls");
      if (previous) previous.replaceWith(group);
      else article.append(group);
    }
  }
}

function cloneWithoutIds(node) {
  const copy = node.cloneNode(true);
  copy.removeAttribute("id");
  copy.querySelectorAll("[id]").forEach((child) => child.removeAttribute("id"));
  return copy;
}

const GLANCE_STYLE = `
#report-summary .glance{margin:12px 0 20px;border:1px solid #d6e2f7;border-radius:12px;overflow:hidden;background:#fff}
#report-summary .glance-title{margin:0;padding:9px 14px;font-size:15px;background:#eef3ff;color:#173f80}
#report-summary .glance .table-wrap{margin:0;border:0;border-radius:0}
#report-summary .glance table{width:100%;margin:0;border-collapse:collapse;font-size:15px;line-height:1.55}
#report-summary .glance th{padding:7px 12px;font-size:12px;color:#62718a;background:#fafcff;text-align:left;white-space:nowrap;border:0;border-bottom:1px solid #e0e8f5}
#report-summary .glance td{padding:8px 12px;vertical-align:top;border:0;border-bottom:1px solid #eef2f8}
#report-summary .glance tr:last-child td{border-bottom:0}
#report-summary .glance td:first-child{width:1%;white-space:nowrap;font-weight:700;color:#245bda}
#report-summary .glance td:last-child{font-weight:600;color:#14233d}
#report-summary .summary-points{gap:6px;margin:12px 0 20px}
#report-summary .summary-points>li{padding:8px 12px 8px 44px;font-size:15px;line-height:1.6}
#report-summary .summary-points>li::before{top:9px;left:12px}
#report-summary .summary-point-title{display:none}
#report-summary .summary-points p{margin:2px 0}
#report-summary .reader-summary-list>li.is-read>a{opacity:.6}`;

function glanceTable(doc, report) {
  return [...doc.querySelectorAll(`#${CSS.escape(`run-${report.id}`)} .report-body table`)].find((table) =>
    /判断|takeaway/i.test(table.tHead?.textContent || table.rows[0]?.textContent || ""));
}

function renderSummary(doc, report, summary) {
  if (!doc.getElementById("reader-glance-style")) {
    const style = doc.createElement("style");
    style.id = "reader-glance-style";
    style.textContent = GLANCE_STYLE;
    doc.head.append(style);
  }
  summary.replaceChildren();
  const original = doc.getElementById(`run-${report.id}`);
  const title = doc.createElement("h2");
  title.textContent = `${report.name} · 总结`;
  summary.append(title);
  const progress = doc.createElement("p");
  progress.className = "muted";
  progress.textContent = `当前文章已读 ${report.progress.read_articles}/${report.progress.articles} 篇 · 收藏 ${report.progress.favorite_articles} 篇`;
  summary.append(progress);
  const table = glanceTable(doc, report);
  if (table) {
    const glance = doc.createElement("section");
    glance.className = "glance";
    const heading = doc.createElement("h3");
    heading.className = "glance-title";
    heading.textContent = "一眼速览（原报告排序表）";
    glance.append(heading, cloneWithoutIds(table.closest(".table-wrap") || table));
    summary.append(glance);
  } else {
    const heading = doc.createElement("h3");
    heading.textContent = "原报告摘要（节选）";
    summary.append(heading);
    const card = [...doc.querySelectorAll("#daily-overview .digest")].find((node) =>
      node.querySelector(".digest-heading a")?.getAttribute("href") === `#run-${report.id}`);
    const excerpt = card?.querySelector(".summary-points");
    if (excerpt?.childElementCount) summary.append(cloneWithoutIds(excerpt));
    else {
      const empty = doc.createElement("p");
      empty.textContent = "该报告没有可提取的摘要，请从下方目录阅读原文。";
      summary.append(empty);
    }
  }
  for (const node of original.querySelectorAll(":scope > .meta:first-of-type, :scope > .notice")) {
    summary.append(cloneWithoutIds(node));
  }
  const directory = doc.createElement("h3");
  directory.textContent = state.filter === "favorites" ? "收藏文章" : "文章目录";
  const list = doc.createElement("ol");
  list.className = "reader-summary-list";
  const ordered = readLast(report.articles.map((item, position) => ({ item, position })),
    ({ item }) => item.retired || isRead(item));
  ordered.forEach(({ item, position }) => {
    if (state.filter === "favorites" && !item.favorite) return;
    const row = doc.createElement("li");
    if (item.retired || isRead(item)) row.className = "is-read";
    row.value = position + 1;
    const link = doc.createElement("a");
    link.href = `#${item.anchor}`;
    link.textContent = `${item.retired ? "旧版收藏" : `文章 ${position + 1}`} · ${item.title}`;
    row.append(link);
    const actions = doc.createElement("div");
    actions.className = "reader-summary-actions";
    if (!item.retired) actions.append(badge(item, doc));
    actions.append(favoriteButton(doc, report, item));
    row.append(actions);
    const reason = doc.getElementById(item.anchor).querySelector(".favorite-reason");
    if (reason) row.append(cloneWithoutIds(reason));
    list.append(row);
  });
  summary.append(directory, list);
  if (!list.childElementCount) {
    const empty = doc.createElement("p");
    empty.className = "muted";
    empty.textContent = "这份报告没有收藏的文章；切换“全部”可查看完整文章目录。";
    summary.append(empty);
  }
}

function renderCurrentView(doc) {
  const selectedReport = state.document.items.find((item) => item.id === state.view.runId);
  if (selectedReport && wholeReport(selectedReport)) state.view.articleId = null;
  const { runId, articleId } = state.view;
  const report = state.document.items.find((item) => item.id === runId);
  const article = report?.articles.find((item) => item.id === articleId);
  if ((runId && !report) || (articleId && !article)) {
    throw new Error("当前保存的报告中找不到此文章或 Automation。请从左侧重新选择；原文和阅读记录未改动。");
  }
  doc.body.classList.toggle("reader-focused", Boolean(runId));
  for (const node of doc.querySelectorAll("main > article")) {
    const run = state.document.items.find((item) => item.id === node.dataset.runId);
    node.hidden = (Boolean(runId) && ((!articleId && !wholeReport(report)) || node.dataset.runId !== runId)) ||
      (!runId && Boolean(run) && !kindVisible(run.name));
  }
  for (const card of doc.querySelectorAll("#daily-overview .digest, .toc a[href^='#run-']")) {
    const id = (card.querySelector(".digest-heading a") || card).getAttribute("href")?.replace("#run-", "");
    const run = state.document.items.find((item) => item.id === id);
    card.hidden = Boolean(run) && !kindVisible(run.name);
  }
  for (const node of doc.querySelectorAll(".article-unit")) {
    node.hidden = Boolean(articleId) && node.id !== article.anchor;
  }
  for (const body of doc.querySelectorAll(".report-body")) {
    body.hidden = Boolean(articleId) && !body.querySelector(".article-unit:not([hidden])");
  }
  for (const details of doc.querySelectorAll("article details")) {
    const bodies = [...details.querySelectorAll(".report-body")];
    details.hidden = Boolean(articleId) && bodies.length > 0 && bodies.every((body) => body.hidden);
    if (articleId && !details.hidden && bodies.length) details.open = true;
  }
  let summary = doc.getElementById("report-summary");
  if (!summary) {
    summary = doc.createElement("section");
    summary.id = "report-summary";
    summary.className = "card";
    doc.querySelector("main").prepend(summary);
  }
  summary.hidden = !report || Boolean(articleId) || wholeReport(report);
  if (report && !articleId && !wholeReport(report)) renderSummary(doc, report, summary);
  const crumbs = $("breadcrumbs");
  crumbs.replaceChildren();
  const dateLink = document.createElement("button");
  dateLink.type = "button";
  dateLink.textContent = `${state.selected} · 当天合辑`;
  dateLink.addEventListener("click", () => openView());
  crumbs.append(dateLink);
  if (report) {
    crumbs.append(document.createTextNode(" › "));
    const reportLink = document.createElement("button");
    reportLink.type = "button";
    reportLink.textContent = report.name;
    reportLink.addEventListener("click", () => openView(report.id));
    if (!article) reportLink.setAttribute("aria-current", "page");
    crumbs.append(reportLink);
  }
  if (article) {
    crumbs.append(document.createTextNode(" › "));
    const current = document.createElement("span");
    current.textContent = article.title;
    current.title = article.title;
    current.setAttribute("aria-current", "page");
    crumbs.append(current);
  }
  document.title = `${article?.title || report?.name || state.selected} · AI 报告阅读器`;
}

function openView(runId = null, articleId = null) {
  if (!state.document || state.loading || state.writing) return;
  state.view = { runId, articleId };
  state.expandedDate = state.selected;
  if (runId) state.expandedReports.add(runId);
  try {
    renderCurrentView($("report-frame").contentDocument);
    updateAddress();
    renderSidebar();
    setControls();
    document.body.classList.remove("sidebar-open");
    $("report-frame").contentWindow.scrollTo({ top: 0, behavior: "instant" });
  } catch (error) {
    reportError(error);
  }
}

function installDocumentLinks(doc) {
  doc.addEventListener("click", (event) => {
    const link = event.target.closest("a[href^='#']");
    if (!link || !state.view.runId) return;
    const target = doc.getElementById(decodeURIComponent(link.getAttribute("href").slice(1)));
    if (!target) return;
    event.preventDefault();
    const run = target.closest("article[data-run-id]");
    const unit = target.closest(".article-unit");
    openView(run?.dataset.runId || null, unit?.dataset.articleId || null);
    if (unit || !run) target.scrollIntoView({ block: "start", behavior: "instant" });
  });
}

function renderSelectedMeta(context) {
  $("selected-meta").replaceChildren(badge(context));
  const detail = document.createElement("span");
  detail.textContent = `已读 ${context.read_reports}/${context.reports} 份` +
    ` · 文章 ${context.read_articles}/${context.articles} 篇` +
    (context.updated_reports ? ` · ${context.updated_reports} 份有更新` : "") + ` · ${context.path}`;
  $("selected-meta").append(detail);
}

async function syncReadingDocument(current) {
  const html = await api(`documents/${current.date}`);
  if (state.document !== current) return;
  const next = new DOMParser().parseFromString(html, "text/html");
  const node = next.getElementById("reader-context");
  if (!node) throw new Error("报告缺少阅读状态，无法同步显示。");
  const context = JSON.parse(node.textContent);
  const sameItems = context.items.length === current.items.length && context.items.every((report, index) => {
    const previous = current.items[index];
    return report.id === previous.id && report.articles.length === previous.articles.length &&
      report.articles.every((item, position) => {
        const old = previous.articles[position];
        return item.id === old.id && item.retired === old.retired &&
          item.favorite === old.favorite && item.favorite_reason === old.favorite_reason;
      });
  });
  if (context.date !== current.date || !context.source_revision ||
      context.source_revision !== current.source_revision || !sameItems) {
    throw new Error("报告正文、目录或收藏已变化，需要重新加载查看最新内容。");
  }
  const doc = $("report-frame").contentDocument;
  const selectors = ["body > header", "#daily-overview", "main > footer"];
  for (const report of context.items) {
    selectors.push(`#${CSS.escape(`run-${report.id}`)} > h2`);
    for (const item of report.articles) {
      selectors.push(`#${CSS.escape(item.anchor)} > .article-reading-status`);
    }
  }
  const patches = selectors.map((selector) => {
    const target = doc.querySelector(selector);
    const source = next.querySelector(selector);
    if (!target || !source) throw new Error("报告缺少对应的阅读状态位置。");
    return { target, source };
  });
  // Keep the iframe and article bodies intact; only reconcile saved state and progress.
  for (const { target, source } of patches) {
    target.replaceChildren(...[...source.childNodes].map((node) => doc.importNode(node, true)));
  }
  doc.querySelectorAll("main > .notice").forEach((node) => node.remove());
  for (const node of next.querySelectorAll("main > .notice")) {
    doc.getElementById("daily-overview").before(doc.importNode(node, true));
  }
  doc.getElementById("reader-context").textContent = JSON.stringify(context);
  state.document = context;
  installReportControls(doc, context);
  if (state.view.runId && !state.view.articleId &&
      !wholeReport(context.items.find((report) => report.id === state.view.runId))) {
    renderSummary(doc, context.items.find((report) => report.id === state.view.runId),
      doc.getElementById("report-summary"));
  }
  renderSelectedMeta(context);
}

$("report-frame").addEventListener("load", () => {
  if (!state.selected) return;
  try {
    const doc = $("report-frame").contentDocument;
    const node = doc?.getElementById("reader-context");
    if (!node) {
      let message = "报告加载失败；请点击刷新，或检查本地服务窗口中的错误。";
      if (doc?.body?.textContent?.trim().startsWith("{")) {
        message = JSON.parse(doc.body.textContent).message || message;
      }
      throw new Error(message);
    }
    const context = JSON.parse(node.textContent);
    if (context.date !== state.selected) return;
    if (typeof context.source_revision !== "string") {
      throw new Error("阅读器服务仍为旧版本。请用桌面快捷方式重新打开阅读器；仅刷新网页不会升级服务。");
    }
    if (context.items.some((report) => typeof report.whole_report !== "boolean")) {
      throw new Error("阅读器服务需要重启才能按整篇读取 Market Briefing。请用桌面快捷方式重新打开。");
    }
    installReportControls(doc, context);
    installDocumentLinks(doc);
    state.document = context;
    state.loading = false;
    for (const op of pendingFor(context.date)) applyLocalReading(context, op);
    installReportControls(doc, context);
    if (state.filter === "favorites") {
      for (const report of context.items) if (report.progress.favorite_articles) state.expandedReports.add(report.id);
    }
    renderCurrentView(doc);
    renderSidebar();
    $("report-frame").hidden = false;
    $("document-placeholder").hidden = true;
    renderSelectedMeta(context);
    $("reader-status").textContent = "日期 → Automation 总结 → 文章；已读、收藏与原因保存在 OneDrive HTML 中。";
    if (context.needs_refresh) {
      notice("这份报告的位置与上次索引不同。点击“刷新”同步状态。", "warning");
    }
    setControls();
    doc.documentElement.style.scrollBehavior = "auto";
    $("report-frame").contentWindow.scrollTo(0, state.scrollTop);
    doc.documentElement.style.removeProperty("scroll-behavior");
  } catch (error) {
    state.loading = false;
    state.document = null;
    renderSidebar();
    $("document-placeholder").hidden = false;
    $("placeholder-title").textContent = "暂时无法显示报告";
    $("placeholder-text").textContent = error.message;
    setControls();
    reportError(error);
  }
});

function applyLocalReading(context, op) {
  const report = context.items.find((entry) => entry.id === op.run_id);
  if (!report) return;
  const current = report.articles.filter((entry) => !entry.retired);
  for (const entry of op.article_id ? current.filter((entry) => entry.id === op.article_id) : current) {
    entry.state = op.read ? "read" : "unread";
    entry.updated = false;
  }
  const read = current.filter(isRead).length;
  const updated = current.filter((entry) => entry.updated).length;
  Object.assign(report.progress, { read_articles: read, updated_articles: updated,
    unread_articles: current.length - read - updated });
  if (op.article_id) {
    report.state = read === current.length ? "read" : "unread";
    report.updated = report.updated && updated > 0;
  } else {
    report.state = op.read ? "read" : "unread";
    report.updated = false;
  }
  const day = {
    read_reports: context.items.filter(isRead).length,
    updated_reports: context.items.filter((entry) => entry.updated).length,
    articles: 0, read_articles: 0,
  };
  for (const entry of context.items) {
    day.articles += wholeReport(entry) ? 1 : entry.progress.articles;
    day.read_articles += wholeReport(entry) ? Number(isRead(entry)) : entry.progress.read_articles;
  }
  day.unread_reports = context.reports - day.read_reports - day.updated_reports;
  day.updated = day.updated_reports > 0;
  day.state = !context.reports ? "empty" : day.read_reports === context.reports ? "read" :
    day.read_reports || day.updated_reports || day.read_articles ? "partial" : "unread";
  Object.assign(context, day);
  const entry = state.catalog?.entries.find((candidate) => candidate.date === context.date);
  if (entry) {
    Object.assign(entry, day);
    for (const info of entry.reports_info || []) {
      const item = context.items.find((candidate) => candidate.id === info.id);
      if (item) info.read = isRead(item);
    }
  }
}

function pendingFor(date) {
  return writes.queue.filter((op) => op.date === date);
}

function renderReadingState() {
  const doc = $("report-frame").contentDocument;
  if (state.document && doc && !state.loading) {
    installReportControls(doc, state.document);
    const report = state.document.items.find((entry) => entry.id === state.view.runId);
    if (report && !state.view.articleId && !wholeReport(report)) {
      renderSummary(doc, report, doc.getElementById("report-summary"));
    }
    renderSelectedMeta(state.document);
  }
  renderSidebar();
  setControls();
}

function writeStatus() {
  $("reader-status").textContent = writes.queue.length ?
    `正在后台保存 ${writes.queue.length} 项阅读标记…可继续阅读。` : "阅读标记已保存到 OneDrive HTML。";
}

function queueReading(read, report, item) {
  if (!state.document || state.loading) return;
  const date = state.document.date;
  if (!pendingFor(date).length) {
    writes.revisions.set(date, { revision: state.document.revision, source: state.document.source_revision });
  }
  const op = { date, run_id: report.id, article_id: item?.id ?? null, read };
  writes.queue.push(op);
  applyLocalReading(state.document, op);
  renderReadingState();
  writeStatus();
  drainWrites();
}

function waitForWrites() {
  return writes.running ? new Promise((resolve) => writes.idle.push(resolve)) : Promise.resolve();
}

async function saveReading(op) {
  const base = writes.revisions.get(op.date);
  const data = { date: op.date, revision: base.revision, run_id: op.run_id, read: op.read };
  if (op.article_id !== null) data.article_id = op.article_id;
  const route = op.article_id !== null ? "api/article-reading" : "api/report-reading";
  for (let attempt = 0; ; attempt += 1) {
    try {
      const catalog = await api(route, data);
      let next = catalog.document;
      if (!next) {
        const html = await api(`documents/${op.date}`);
        next = JSON.parse(new DOMParser().parseFromString(html, "text/html")
          .getElementById("reader-context").textContent);
      }
      if (next.source_revision !== base.source) {
        const error = new Error("阅读标记已保存，但报告正文已有新内容。请重新加载当前报告，读过后再标记。");
        error.code = "report_changed";
        throw error;
      }
      writes.revisions.set(op.date, { revision: next.revision, source: base.source });
      if (state.document?.date === op.date) state.document.revision = next.revision;
      return catalog;
    } catch (error) {
      // Only a lock held by another archive writer is retried; the server still verifies the revision.
      if (error.code !== "archive_busy" || attempt >= 5) throw error;
      await new Promise((resolve) => setTimeout(resolve, 1500));
    }
  }
}

async function drainWrites() {
  if (writes.running) return;
  writes.running = true;
  let catalog = null;
  const touched = new Set();
  try {
    while (writes.queue.length) {
      const op = writes.queue[0];
      try {
        catalog = await saveReading(op);
        touched.add(op.date);
        writes.queue.shift();
        writeStatus();
      } catch (error) {
        const dropped = pendingFor(op.date).length;
        writes.queue = writes.queue.filter((pending) => pending.date !== op.date);
        writes.revisions.delete(op.date);
        notice(`${error.message || "保存失败。"}${dropped > 1 ? `（${op.date} 另有 ${dropped - 1} 项未保存的标记已撤回）` : ""}`,
          "error", true);
        $("reader-status").textContent = "有阅读标记未保存；已重新加载显示真实状态。";
        if (state.selected === op.date) selectDate(op.date, $("report-frame").contentWindow?.scrollY || 0, state.view, false);
      }
    }
    if (catalog) applyCatalog(catalog);
    const current = state.document;
    if (current && !state.loading && touched.has(current.date) && !writes.queue.length) {
      try {
        await syncReadingDocument(current);
        for (const op of pendingFor(current.date)) applyLocalReading(state.document, op);
        renderReadingState();
      } catch (error) {
        reportError(error, true);
      }
    }
  } finally {
    writes.running = false;
    for (const resolve of writes.idle.splice(0)) resolve();
    if (writes.queue.length) drainWrites();
  }
}

window.addEventListener("beforeunload", (event) => {
  if (writes.queue.length) event.preventDefault();
});

async function changeReadingState(read, report = null, item = null) {
  if (report) {
    queueReading(read, report, item);
    return;
  }
  await waitForWrites();
  if (!state.document || state.writing || state.loading) return;
  const current = state.document;
  const frame = $("report-frame");
  const focusDoc = document.activeElement === frame ? frame.contentDocument : document;
  const focused = focusDoc.activeElement;
  const focusKey = focused?.dataset.navKey ||
    focused?.closest(".article-controls")?.querySelector("[data-nav-key]")?.dataset.navKey;
  let saved = false;
  state.writing = true;
  setControls();
  $("reader-status").textContent = "正在保存阅读标记…";
  try {
    const data = { date: current.date, revision: current.revision };
    const route = item ? "api/article-reading" : report ? "api/report-reading" : read ? "api/archive" : "api/unarchive";
    if (item) Object.assign(data, { run_id: report.id, article_id: item.id, read });
    else if (report) Object.assign(data, { run_id: report.id, read });
    const catalog = await api(route, data);
    saved = true;
    await syncReadingDocument(current);
    applyCatalog(catalog);
    notice(report ? "" :
      read ? `${current.date} 全部已保存报告已标为已读，并移入“已读”目录。` :
        `${current.date} 全部报告已设为未读，并移回根目录。原始报告和历史均保留。`);
    $("reader-status").textContent = report ? `本篇已设为${read ? "已读" : "未读"}，阅读进度已同步。` :
      "整天阅读状态和文件位置已保存。";
  } catch (error) {
    reportError(error, saved);
  } finally {
    state.writing = false;
    setControls();
    if ((focusDoc === document || document.activeElement === frame) &&
        (focusDoc.activeElement === focusDoc.body || focusDoc.activeElement === focused)) {
      const target = focused?.isConnected ? focused :
        focusKey ? [...focusDoc.querySelectorAll(`[data-nav-key="${CSS.escape(focusKey)}"]`)]
          .find((node) => node.getClientRects().length) : null;
      if (target && !target.disabled) target.focus({ preventScroll: true });
    }
  }
}

async function refresh() {
  if (state.writing) return;
  await waitForWrites();
  if (state.writing) return;
  state.writing = true;
  setControls();
  try {
    const catalog = await api("api/refresh", {});
    state.writing = false;
    applyCatalog(catalog);
    const key = state.selected || catalog.entries[0]?.date;
    if (key) selectDate(key, 0, state.view);
    notice("目录和阅读状态已刷新；没有自动确认任何未读报告或新内容。");
  } catch (error) {
    reportError(error);
  } finally {
    state.writing = false;
    setControls();
  }
}

function openFavorite(report, item) {
  if (!state.document || state.loading || state.writing) return;
  state.favorite = { context: state.document, report, item };
  $("favorite-title").textContent = item.favorite ? "编辑收藏原因" : "收藏文章";
  $("favorite-article-title").textContent = `${report.name} · ${item.title}`;
  $("favorite-reason").value = item.favorite_reason;
  $("favorite-remove").hidden = !item.favorite;
  $("favorite-error").hidden = true;
  $("favorite-dialog").showModal();
  $("favorite-reason").focus();
}

async function saveFavorite(favorite) {
  if (!state.favorite || state.writing) return;
  await waitForWrites();
  if (!state.favorite || state.writing) return;
  const { context, report, item } = state.favorite;
  state.writing = true;
  setControls();
  const revision = state.document?.date === context.date ? state.document.revision : context.revision;
  const scrollTop = $("report-frame").contentWindow.scrollY;
  $("favorite-error").hidden = true;
  try {
    const catalog = await api("api/article-favorite", {
      date: context.date, revision, run_id: report.id, article_id: item.id,
      favorite, reason: favorite ? $("favorite-reason").value : item.favorite_reason
    });
    const view = !favorite && item.retired && state.view.articleId === item.id ?
      { runId: report.id, articleId: null } : state.view;
    state.writing = false;
    $("favorite-dialog").close();
    applyCatalog(catalog);
    selectDate(context.date, scrollTop, view);
    notice(favorite ? "收藏和原因已保存到 OneDrive HTML，阅读状态没有改变。" :
      "已取消收藏；原文、之前的原因和阅读状态均保留。");
  } catch (error) {
    $("favorite-error").textContent = error.message;
    $("favorite-error").hidden = false;
    reportError(error);
  } finally {
    state.writing = false;
    setControls();
  }
}

$("favorite-form").addEventListener("submit", (event) => { event.preventDefault(); saveFavorite(true); });
$("favorite-remove").addEventListener("click", () => saveFavorite(false));
$("favorite-cancel").addEventListener("click", () => $("favorite-dialog").close());
$("favorite-dialog").addEventListener("close", () => { state.favorite = null; });
$("favorite-dialog").addEventListener("cancel", (event) => { if (state.writing) event.preventDefault(); });
$("archive").addEventListener("click", () => changeReadingState(true));
$("unarchive").addEventListener("click", () => changeReadingState(false));
$("refresh").addEventListener("click", refresh);
$("reload-report").addEventListener("click", () => {
  notice("已重新请求最新正文。请读完后再标记对应报告。", "warning");
  if (state.selected) selectDate(state.selected, 0, state.view);
});
$("dismiss-message").addEventListener("click", () => notice(""));
document.querySelectorAll("[data-kind]").forEach((node) => {
  node.addEventListener("change", () => {
    if (node.checked) state.kinds.add(node.dataset.kind);
    else state.kinds.delete(node.dataset.kind);
    renderSidebar();
    if (state.document && !state.loading) renderCurrentView($("report-frame").contentDocument);
    updateAddress();
    setControls();
  });
});
document.querySelectorAll("[data-filter]").forEach((node) => {
  node.addEventListener("click", () => {
    state.filter = node.dataset.filter;
    if (state.filter === "favorites") {
      for (const report of state.document?.items || []) {
        if (report.progress.favorite_articles) state.expandedReports.add(report.id);
      }
    }
    renderSidebar();
    if (state.document) renderCurrentView($("report-frame").contentDocument);
    updateAddress();
    setControls();
  });
});
$("toggle-sidebar").addEventListener("click", () => document.body.classList.toggle("sidebar-open"));
$("sidebar-scrim").addEventListener("click", () => document.body.classList.remove("sidebar-open"));

async function start() {
  try {
    const catalog = await api("api/catalog");
    const params = new URLSearchParams(location.search);
    const filter = params.get("filter");
    if (filter && !["all", "unread", "archived", "favorites"].includes(filter)) {
      throw new Error("不支持此阅读筛选，请从有效的阅读器地址重新打开。");
    }
    state.filter = filter || "all";
    if (params.has("kinds")) {
      state.kinds = new Set(params.get("kinds").split(",").filter((kind) => kind in KINDS));
      document.querySelectorAll("[data-kind]").forEach((node) => { node.checked = state.kinds.has(node.dataset.kind); });
    }
    applyCatalog(catalog);
    const requested = params.get("date");
    const key = catalog.entries.find((item) => item.date === requested)?.date || filteredEntries()[0]?.date;
    if (key) selectDate(key, 0, { runId: params.get("report"), articleId: params.get("article") });
    else {
      $("placeholder-title").textContent = "还没有可阅读的报告";
      $("placeholder-text").textContent = "每日 Automation 生成报告后，点击刷新。";
    }
  } catch (error) {
    $("placeholder-title").textContent = "无法读取归档目录";
    $("placeholder-text").textContent = error.message;
    reportError(error);
  }
}

start();
