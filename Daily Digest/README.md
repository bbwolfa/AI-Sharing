# Weekly AI Briefing Automation

这是一个可以直接提交到 Git Repo 的 AI 简报 Automation、HTML 生成程序和本地阅读器示例。

## Repo 内容

- `AUTOMATIONS.md`：4 个 Automation 的配置、完整 Prompt、运行顺序和导入说明。
- `2026-09-29.html`：一份完整的汇总 HTML Sample。
- `_tools/archive_reports.py`：从本机 Copilot Automation 运行记录增量生成每日 HTML 和总索引。
- `_tools/reader_server.py`、`_tools/reader/`：支持逐篇已读、收藏和原因编辑的本地阅读器。
- `去重台账/`：GPT 与 Opus 共享的已覆盖内容记录，用于避免重复推荐。
- `打开报告阅读器.cmd`、`刷新阅读状态.cmd`：Windows 快捷入口。

Repo 不包含历史 HTML、`_history`、Copilot 数据库、session-state、迁移备份、锁文件、Python 缓存或 ZIP。

## 查看 Sample

直接打开：

```text
2026-09-29.html
```

该文件是完整生成结果，不是简化模板。它展示了多份 Automation 报告如何汇总到同一个日期 HTML。

## 安装本地工具

```powershell
python -m pip install -r .\_tools\requirements.txt
```

当目标机器已经生成自己的 `index.html` 和日报后，可以运行：

```powershell
.\打开报告阅读器.cmd
```

阅读器只监听本机，并为每次启动生成随机 URL token。

## 导入 Automations

把 `AUTOMATIONS.md` 交给目标 AI，要求它按文件创建 Automations。导入前需要替换：

- `{{BRIEFING_ROOT}}`：Repo 在目标机器上的绝对路径。
- `{{COPILOT_DATA_DB}}`：目标用户自己的 Copilot `data.db`。
- `{{COPILOT_SESSION_STATE}}`：目标用户自己的 Copilot session-state 路径。
- `{{EXCLUDED_SOURCE_ID}}`：目标用户自己的排除来源；没有时删除对应专用规则。

建议按以下顺序配置：

1. `AI Learning - GPT`：每天 06:30。
2. `AI Market Briefing`：每天 07:00。
3. `AI Learning - Opus`：每天 07:30。
4. `每日 Automation HTML 归档`：每天 09:00。

前三个任务负责生成内容。归档任务负责生成目标用户自己的每日 HTML 和 `index.html`。

## 初始化去重台账

Repo 中的 `covered-seed.jsonl`、`covered-gpt.jsonl` 和 `covered-opus.jsonl` 是空白模板，不包含分享者的历史记录。

Automation 会按 `AUTOMATIONS.md` 中定义的 JSONL 格式逐行追加目标用户自己的入选内容。字段说明与示例见 `去重台账/README.md`。

## 注意

归档任务依赖 Copilot 本地数据库结构和 session-state。必须确认路径与数据格式兼容后再启用。

不要让两台机器同时写入同一个 OneDrive 或同步目录。公开 Repo 前，也应确认 Sample HTML 和去重台账中的内容适合公开。
