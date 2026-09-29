# 去重台账

这里提供空白台账，用于避免 GPT 与 Opus 重复推荐已经覆盖的内容。

## 文件

- `ai-learning/covered-seed.jsonl`：可选的初始禁止名单，默认只读。
- `ai-learning/covered-gpt.jsonl`：GPT 广度扫描任务追加的条目。
- `ai-learning/covered-opus.jsonl`：Opus 深度任务追加的条目。
- `market/daily-briefing-log.md`：市场简报的可选人工记录模板。

三个 JSONL 文件初始为空。每行写入一个独立 JSON 对象：

```json
{"id":"owner/repo","kind":"github","title":"Example project","first_seen":"2026-09-29","seen_dates":["2026-09-29"],"source":"gpt"}
```

`kind` 支持：

- `arxiv`：`id` 使用 arXiv 编号。
- `github`：`id` 使用 `owner/repo`。
- `web`：`id` 使用规范化 URL。

必须使用追加写入，不能覆盖、重排或跳过损坏行。
