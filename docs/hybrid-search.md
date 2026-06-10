# Hybrid Search (optional)

> Status: opt-in, off by default. The default search behavior is unchanged
> (keyword). Turn this on only if you want it.

## What it is

Engram's default search is a keyword scorer (character n-grams + alias
expansion) that is already strong on Chinese. **Hybrid search** fuses three
signals with Reciprocal Rank Fusion (RRF, k=60):

1. **keyword** — the existing token-overlap scorer.
2. **fts** — SQLite FTS5 full-text (now CJK-bigram tokenized).
3. **vector** — semantic embeddings (optional `[vector]` extra).

The index is a **rebuildable** SQLite file next to your JSON store. JSON
stays the single source of truth — delete the index and it rebuilds from
JSON, losing nothing.

## When to use it

Validated win: **cross-lingual retrieval** — e.g. an English query finding
a Chinese note (or vice versa), which keyword search structurally cannot do.
For pure same-language lookups, keyword is already at ceiling on
small/clean stores, so the gain is marginal.

## Enable

Easiest path: `engram setup` offers an optional **Enhanced search** step —
one keystroke enables hybrid, offers the `[vector]` install, writes
`ENGRAM_SEARCH=hybrid` into your AI clients' MCP configs, and builds the
index. Manual path:

```bash
# 1. install the semantic layer (sqlite-vec + FastEmbed)
pip install "piia-engram[vector]"

# 2. turn hybrid on for the process
export ENGRAM_SEARCH=hybrid          # Windows: set ENGRAM_SEARCH=hybrid

# 3. (optional) build the index now; it also builds lazily on first search
engram reindex
```

The first run downloads the embedding model (~90 MB). To keep it off the
system drive, point the cache elsewhere before running:

```bash
export FASTEMBED_CACHE_PATH=/path/to/cache   # e.g. E:\ml-cache\fastembed
```

Without the `[vector]` extra, hybrid still runs on keyword + FTS only (no
download); the cross-lingual benefit needs the vector layer.

## Model

Default: `BAAI/bge-small-zh-v1.5` (Chinese-first, 512-dim). Override:

```bash
export ENGRAM_EMBED_MODEL=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
```

Changing the model rebuilds the vector table automatically (no dim-mismatch
crash).

## How it stays fresh

The index carries a content + embedding-model + vector-backend fingerprint.
It rebuilds automatically when your knowledge changes, when you switch
`ENGRAM_EMBED_MODEL`, or when you install the `[vector]` extra after a
FTS-only build. You can also force a rebuild any time with `engram reindex`.
