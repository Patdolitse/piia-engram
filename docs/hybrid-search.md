# Hybrid Search (optional) / 混合搜索（可选）

> Status: opt-in, off by default. The default search behavior is unchanged
> (keyword). Turn this on only if you want it.
>
> 状态：opt-in，默认关闭。不开启时搜索行为完全不变（关键词）。需要才开。

## What it is / 是什么

Engram's default search is a keyword scorer (character n-grams + alias
expansion) that is already strong on Chinese. **Hybrid search** fuses three
signals with Reciprocal Rank Fusion (RRF, k=60):

1. **keyword** — the existing token-overlap scorer.
2. **fts** — SQLite FTS5 full-text (now CJK-bigram tokenized).
3. **vector** — semantic embeddings (optional `[vector]` extra).

The index is a **rebuildable** SQLite file next to your JSON store. JSON
stays the single source of truth — delete the index and it rebuilds from
JSON, losing nothing.

Engram 默认用关键词检索（字符 n-gram + 别名扩展），在中文上已经很强。**混合搜索**用 RRF（k=60）融合三路信号：关键词、FTS5 全文（已做 CJK 二元分词）、语义向量（可选）。索引是可重建的 SQLite 文件，**JSON 仍是唯一事实源**，删掉能从 JSON 重建。

## When to use it / 何时用

Validated win: **cross-lingual retrieval** — e.g. an English query finding
a Chinese note (or vice versa), which keyword search structurally cannot do.
For pure same-language lookups, keyword is already at ceiling on small/clean
stores, so the gain is marginal.

实测收益：**跨语言检索**——比如英文 query 命中中文知识（反之亦然），这是关键词检索做不到的。同语言查找上关键词已基本封顶，增益有限。

## Enable / 开启

```bash
# 1. install the semantic layer (sqlite-vec + FastEmbed)
pip install "piia-engram[vector]"

# 2. turn hybrid on for the process
export ENGRAM_SEARCH=hybrid          # Windows: set ENGRAM_SEARCH=hybrid

# 3. (optional) build the index now; it also builds lazily on first search
engram reindex
```

First run downloads the embedding model (~90 MB). To keep it off the system
drive, point the cache elsewhere before running:

首次运行会下载嵌入模型（约 90 MB）。想放到非系统盘，先设缓存目录：

```bash
export FASTEMBED_CACHE_PATH=/path/to/cache   # e.g. E:\ml-cache\fastembed
```

Without the `[vector]` extra, hybrid still runs on keyword + FTS only (no
download); the cross-lingual benefit needs the vector layer.

不装 `[vector]` 时，hybrid 只用 关键词 + FTS（不下载模型）；跨语言收益需要向量层。

## Model / 模型

Default: `BAAI/bge-small-zh-v1.5` (Chinese-first, 512-dim). Override:

默认 `BAAI/bge-small-zh-v1.5`（中文优先，512 维）。覆盖：

```bash
export ENGRAM_EMBED_MODEL=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
```

Changing the model rebuilds the vector table automatically (no dim-mismatch
crash). 换模型会自动重建向量表，不会因维度不一致报错。
