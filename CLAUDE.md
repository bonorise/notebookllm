# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

电子书批量笔记生成系统 — 调用 NotebookLM CLI 为本地电子书**按书名自适应选择 3-4 个分析模型**，生成全套内容。

每本书约 10-14 个产物：3-4 份 `.md` 分析报告 + 3-4 张 `.png` 信息图 + 3-4 个 `.pptx` 演示文稿 + 1 份 `.json` 思维导图 + 1 份闪卡 + 3 份测验。

- **项目目录**: 当前目录
- **电子书目录**: `/Users/liubo/Desktop/Learning/reading/PDF/`（可通过 `PDF_DIR` 环境变量覆盖）
- **11 个阅读模型**: `reading_models/`
- **模型选择配置**: `prompts/model_selection.yaml`

## 架构

多文件架构：`batch_generate.py`（主脚本）+ `book_model_selector.py`（模型选择器）+ `migrate_books_db.py`（数据库迁移）。

```
batch_generate.py              # 主脚本 (~1140 行)，8 个子命令 + NotebookLM CLI 调用
book_model_selector.py         # 自适应模型选择：搜索 + DeepSeek/启发式 → 选 3-4 个模型
migrate_books_db.py            # 旧版数据库兼容迁移脚本（独立运行）
books.db                       # SQLite（自动创建），新架构用 3 表 + 1 表
prompts/
  └── model_selection.yaml     # 11 个模型元信息、选择策略、infographic/PPT prompt（核心配置）
reading_models/                # 11 个模型的完整 Prompt 文档
.cache/book_model_selection/   # 模型选择结果缓存（按书名 hash）
PDF/<书名>/                    # 每本书一个子目录（ensure_output_dir 自动创建）
```

### 子命令一览

| 子命令 | 用途 | 关键参数 |
|--------|------|----------|
| `discover` | NotebookLM 笔记本 ↔ 本地电子书模糊匹配 | `--book`, `--limit` |
| `recommend` | **离线**推荐模型（不依赖 NotebookLM） | `--title`, `--no-search`, `--no-llm`, `--json` |
| `select-models` | 为已 discover 的书籍选择并保存模型 | `--book`, `--limit`, `--no-search`, `--force` |
| `generate` | 按**已选模型**生成分析报告 | `--book`, `--limit`, `--retry-failed`, `--force` |
| `infographic` | 按已选模型生成信息图 | `--book`, `--style`, `--force` |
| `slides` | 按已选模型生成演示文稿 | `--book`, `--force` |
| `mindmap` | 生成全书思维导图 | `--book`, `--force` |
| `flashcards` | 生成关键概念闪卡 | `--book`, `--force` |
| `quiz` | 生成测验题（3 难度） | `--book`, `--difficulty`, `--force` |
| `status` | 查看进度（含历史已完成模型） | `--book` |

## 核心工作流

```
discover（匹配书与笔记本）
  ↓
select-models（为每本书从 11 个模型中选 3-4 个）
  ↓
generate（只生成已选模型的报告）
  ↓
infographic / slides / mindmap / flashcards / quiz（并行或逐个）
  ↓
status（验证完整性）
```

`recommend` 命令可**独立于 discover** 运行，只根据书名推荐模型，不依赖 NotebookLM。

## 数据库设计

**3 张新表 + 1 张保留表**（自动创建，`init_db()` 触发）：

```sql
-- 保留：笔记本与电子书匹配关系
books (notebook_id, notebook_title, ebook_filename, ebook_path, matched, ...)

-- 新增：每本书的模型选择结果
model_selections (notebook_id PK, selected_models TEXT, book_profile JSON,
                  search_data JSON, rationale TEXT, source TEXT, ...)

-- 新增：每本书每个模型的运行状态（替代旧的 axiom_status 等宽表列）
model_runs (notebook_id, model_key PK, status, artifact_id, output_path, error_log, ...)

-- 新增：旧版 academic 记录兼容
legacy_academic_runs (notebook_id, status, artifact_id, output_path, ...)
```

**迁移机制**：首次运行 `init_db()` 自动创建新表 + 迁移旧 4 模型状态到 `model_runs`。也可手动 `python3 migrate_books_db.py --db books.db`。

## 关键设计决策

- **自适应选择 vs 全量生成**: 不再每本书跑全部模型，从 11 个中选 3-4 个最合适的，减少重复和成本
- **模型选择器**: `book_model_selector.py` 优先用 DeepSeek API 判断，失败则退回到关键词启发式规则；支持网络搜索增强
- **选择缓存**: `.cache/book_model_selection/` 按书名 hash 缓存，`--force` 强制重新选择
- **生成模式**: `generate`/`infographic` 用 `--wait` 同步等待；`slides`/`flashcards`/`quiz` 用 `--no-wait` + `poll_artifact_complete()` 轮询
- **轮询机制**: 对比 artifact list 前后数量，每 5s 检查一次。演示文稿 max_wait=1200s（20分钟），闪卡/测验 max_wait=600s（10分钟）
- **产物跟踪**: 报告用 SQLite `model_runs` 追踪；slides/flashcards/quiz 加文件存在性做幂等（`output_path.exists()` → 跳过）
- **中断续传**: SQLite + 文件检查，Ctrl+C 安全中断后重新运行跳过已完成
- **速率限制**: `nblm_with_retry` 指数退避重试，模型间 sleep 2s，书本间 sleep 5s
- **输出目录**: `ensure_output_dir(base_name)` 自动创建子目录并将根目录 PDF 移入

## 常用命令

```bash
cd /Users/liubo/Desktop/PROJECT/00tools/notebookllm

# --- 工作流 ---
.venv/bin/python -u batch_generate.py discover              # 匹配书与笔记本
.venv/bin/python -u batch_generate.py recommend --title "大国大城"  # 离线推荐模型
.venv/bin/python -u batch_generate.py select-models --book "大国大城"  # 选择并保存模型
.venv/bin/python -u batch_generate.py status                # 查看进度

# --- 生成（只生成已选模型）---
.venv/bin/python -u batch_generate.py generate --book "大国大城"
.venv/bin/python -u batch_generate.py generate --retry-failed --force
.venv/bin/python -u batch_generate.py infographic --book "大国大城" --style sketch-note
.venv/bin/python -u batch_generate.py slides --book "大国大城"
.venv/bin/python -u batch_generate.py mindmap --book "大国大城"
.venv/bin/python -u batch_generate.py flashcards --book "大国大城"
.venv/bin/python -u batch_generate.py quiz --book "大国大城"

# --- 离线推荐（不依赖 NotebookLM）---
.venv/bin/python -u batch_generate.py recommend --title "大国大城" --json
.venv/bin/python -u batch_generate.py recommend --title "大国大城" --no-llm  # 只用启发式

# --- 数据库迁移 ---
python3 migrate_books_db.py --db books.db                   # 手动迁移

# --- NotebookLM 状态 ---
notebooklm status
notebooklm list --json
```

## 11 个阅读模型

`reading_models/` 目录 + `prompts/model_selection.yaml` 定义，`book_model_selector.py` 负责选择。

| 编号 | 模型 key | 名称 | 核心用途 |
|------|----------|------|----------|
| 01 | `axiom` | 公理化逻辑审计 | 审查论证严密性：假设→公理→推导→结论 |
| 02 | `bayes` | 贝叶斯认知更新 | 判断读后应如何更新原有信念 |
| 03 | `structure` | 全书结构地图 | 看清整体骨架和章节功能 |
| 04 | `first_principles` | 第一性原理重建 | 拆到底层事实后从零重建新框架 |
| 05 | `dialectic` | 黑格尔辩证思辨 | 正题→反题→合题，分析深层张力 |
| 06 | `structure_v2` | 全书结构地图(新版) | 快速建立知识地图，适合首轮阅读 |
| 07 | `causal` | 因果机制 | 分析变量、因果链和反馈回路 |
| 08 | `stakeholder` | 利益相关者与权力结构 | 谁受益、谁受损、谁有权、谁被忽视 |
| 09 | `concept` | 概念工程 | 分析作者如何定义、改造和替换概念 |
| 10 | `practice` | 实践转化 | 把书中思想转化为可执行行动方案 |
| 11 | `blindspot` | 盲区与反向阅读 | 找出作者没说清、回避或简化的地方 |

模型 Prompt 原文在 `reading_models/model01-*.md` ～ `model11-*.md`。

## 产物文件命名

| 产物 | 格式 | 文件名模式 |
|------|------|-----------|
| 分析报告 | `.md` | `<书名>_<模型中文名>.md` |
| 信息图 | `.png` | `<书名>_<模型中文名>_信息图.png` |
| 演示文稿 | `.pptx` | `<书名>_<模型中文名>_演示文稿.pptx` |
| 思维导图 | `.json` | `<书名>_思维导图.json` |
| 闪卡 | `.md` | `<书名>_闪卡.md` |
| 测验 | `.md` | `<书名>_测验_简单.md` / `_中等.md` / `_困难.md` |

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `PDF_DIR` | `/Users/liubo/Desktop/Learning/reading/PDF` | 电子书存放目录 |
| `DEEPSEEK_API_KEY` | 无 | DeepSeek API 密钥（可选，用于智能模型选择） |

## 信息图风格

`--style` 支持 11 种：`auto`, `sketch-note`(默认), `professional`, `bento-grid`, `editorial`, `instructional`, `bricks`, `clay`, `anime`, `kawaii`, `scientific`。
