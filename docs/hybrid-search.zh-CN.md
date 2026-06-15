# 混合检索（可选）

> 状态：按需开启，默认关闭。默认检索行为完全不变（关键词）。
> 只有当你需要时才开启。
> 开启后，常规 `search_knowledge` 路径会使用混合检索；它不是独立的实验工具面。

[English version](hybrid-search.md)

## 它是什么

Engram 的默认检索是关键词打分器（字符 n-gram + 别名扩展），对中文已经很强。
**混合检索**用 Reciprocal Rank Fusion（RRF，k=60）融合三路信号：

1. **keyword** —— 现有的词元重叠打分器。
2. **fts** —— SQLite FTS5 全文检索（已支持 CJK 二元分词）。
3. **vector** —— 语义向量（可选的 `[vector]` extra）。

索引是 JSON 存储旁边一个**可重建**的 SQLite 文件。JSON 始终是唯一数据源——
删掉索引它会从 JSON 重建，什么都不会丢。

## 什么时候用

已验证的收益：**跨语言检索**——例如用英文查询找到中文笔记（反之亦然），
这是关键词检索在结构上做不到的。纯同语言查询场景下，关键词检索在小而干净
的知识库上已接近上限，混合检索的增益有限。

## 开启方式

最简单的路径：`engram setup` 向导提供可选的**增强检索**步骤——一键开启
混合检索、可选安装 `[vector]`、把 `ENGRAM_SEARCH=hybrid` 写进各 AI 客户端
的 MCP 配置并构建索引。手动路径：

```bash
# 1. 安装语义层（sqlite-vec + FastEmbed）
pip install "piia-engram[vector]"

# 2. 为当前进程开启混合检索
export ENGRAM_SEARCH=hybrid          # Windows: set ENGRAM_SEARCH=hybrid

# 3. （可选）立即构建索引；首次搜索时也会自动惰性构建
engram reindex
```

首次运行会下载嵌入模型（约 90 MB）。如果不想占用系统盘，先把缓存指到
别处再运行：

```bash
export FASTEMBED_CACHE_PATH=/path/to/cache   # 例如 /path/to/fastembed-cache
```

不安装 `[vector]` extra 时，混合检索仍以 keyword + FTS 两路运行（无需下载）；
跨语言收益需要向量层。

## 模型

默认：`BAAI/bge-small-zh-v1.5`（中文优先，512 维）。可覆盖：

```bash
export ENGRAM_EMBED_MODEL=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
```

更换模型会自动重建向量表（不会出现维度不匹配崩溃）。

## 它如何保持新鲜

索引带有内容 + 嵌入模型 + 向量后端的指纹。当知识变化、切换
`ENGRAM_EMBED_MODEL`、或在 FTS-only 构建之后补装 `[vector]` extra 时，
索引会自动重建。你也可以随时用 `engram reindex` 强制重建。
