# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

电子书批量笔记生成系统 — 调用 NotebookLM CLI 为本地电子书用 5 种分析模型各生成一份深度分析报告。

每本书生成 5 份 `.md` 分析报告 + 5 张 `.png` 信息图 + 1 份 `.json` 思维导图。

- **项目目录**: 当前目录（`/Users/liubo/Desktop/PROJECT/00tools/notebookllm/`）
- **电子书目录**: `/Users/liubo/Desktop/Learning/reading/PDF/`（数据盘，每本书一个子目录）
- **设计文档**: `DESIGN.md`（原始设计，产物类型已变更）
- **实施计划**: `PLAN.md`（原始计划，子命令结构保留但产物逻辑已变更）
- **Prompt 模板源**: `reading models/`（5 种分析框架的原始 prompt）
- **Prompt 配置**: `prompts/reading_prompts.yaml`（已根据 5 个模型文件生成）

## 阅读模型（Prompt 模板）

`reading models/` 目录包含 5 种分析框架，作为 NotebookLM 生成时的 prompt 输入：

| 模型 | 文件 | 分析框架 | 适用场景 |
|------|------|----------|----------|
| 公理体系 | `model01-axiom.md` | 提取假设→确立公理→还原推导→审视结论 | 逻辑严密的论证型书籍 |
| 贝叶斯推理 | `model02-bayes.md` | 先验信念→新证据→似然更新→后验评估 | 刷新认知、挑战共识的书籍 |
| 学术拆解 | `model03-new.md` | 核心问题→旧范式→缺陷→新主张→对照→论证→延伸→局限 | 提出新理论/框架的学术著作 |
| 第一性原理 | `model04-musk.md` | 识别核心命题→解构假设→回归基本事实→从零重建 | 需要打破惯例、重新思考的书籍 |
| 黑格尔辩证法 | `model05-heiger.md` | 正题→反题→正反交锋→合题→辩证回望 | 内含矛盾张力的思辨型书籍 |

这些模型与 DESIGN.md 中描述的 `prompts/reading_prompts.yaml` 是互补关系：yaml 文件做快速映射（书名→prompt_key），模型文件提供具体 prompt 内容。实施时可根据需要将模型内容整合到 YAML 中，或直接用模型文件路径引用。

## 架构

单脚本架构：`batch_generate.py` + SQLite 进度追踪 + NotebookLM CLI 子进程调用。

```
batch_generate.py           # 唯一的主脚本
  ├── discover 子命令        # 从 NotebookLM 获取笔记本 → 扫描电子书 → 模糊匹配
  ├── generate 子命令        # 逐本生成 5 份分析报告 (.md)
  ├── infographic 子命令     # 为已完成报告生成信息图 (.png)
  ├── mindmap 子命令         # 生成全书思维导图 (.json)
  └── status 子命令          # 从 SQLite 读取进度，表格化展示
books.db                     # SQLite（自动创建），记录每本书每个模型的生成状态
prompts/reading_prompts.yaml # 5 个分析模型的完整 prompt（已创建）
PDF/<书名>/                  # 每本书一个子目录，原书 + 所有产出物
```

### 产物流程（每本书 5 步）

按顺序生成：axiom → bayes → academic → first_principles → dialectic。
每步调用 `notebooklm generate report --format custom "<模型prompt>" --wait --json`，
解析 artifact_id 后 `notebooklm download report <路径>.md --latest`。
完成后立即 commit 到 SQLite，失败记录 error_log 继续下一本。

### 关键设计决策

- **产物类型**: 统一使用 `generate report --format custom`，5 个模型共用同一 CLI 命令，只有 prompt 不同
- **匹配策略**: `difflib.SequenceMatcher` 模糊匹配，阈值 0.8（笔记本标题 vs 电子书文件名去扩展名）
- **中断续传**: SQLite 记录每步状态，Ctrl+C 安全中断后重新运行会跳过已完成的
- **速率限制**: 内置指数退避重试，模型间 sleep 2s，书本间 sleep 5s
- **输出格式**: 所有模型输出 `.md` 文件，文件名格式 `<书名>_<模型名>.md`

## 常用命令

所有命令在项目目录下执行。电子书存放在 `/Users/liubo/Desktop/Learning/reading/PDF/`。

```bash
cd /Users/liubo/Desktop/PROJECT/00tools/notebookllm

# 发现并匹配笔记本与电子书
.venv/bin/python -u batch_generate.py discover

# 查看进度
.venv/bin/python -u batch_generate.py status

# 生成分析报告（每本书 5 个模型）
.venv/bin/python -u batch_generate.py generate
.venv/bin/python -u batch_generate.py generate --limit 3
.venv/bin/python -u batch_generate.py generate --only axiom,bayes
.venv/bin/python -u batch_generate.py generate --retry-failed

# 生成信息图（需先完成报告生成）
.venv/bin/python -u batch_generate.py infographic
.venv/bin/python -u batch_generate.py infographic --style scientific
.venv/bin/python -u batch_generate.py infographic --only axiom

# 生成思维导图（需先完成 discover 匹配）
.venv/bin/python -u batch_generate.py mindmap
.venv/bin/python -u batch_generate.py mindmap --limit 1

# 生成演示文稿（较慢，用 --no-wait + 轮询，每个约 10-20 分钟）
.venv/bin/python -u batch_generate.py slides
.venv/bin/python -u batch_generate.py slides --only axiom

# 检查 NotebookLM 状态
notebooklm status
notebooklm list --json
```

### 模型简称与输出文件名

| 模型 | 简称 | 输出文件 |
|------|------|----------|
| 公理体系分析 | `axiom` | `<书名>_公理体系分析.md` |
| 贝叶斯推理分析 | `bayes` | `<书名>_贝叶斯推理分析.md` |
| 学术拆解分析 | `academic` | `<书名>_学术拆解分析.md` |
| 第一性原理分析 | `first_principles` | `<书名>_第一性原理分析.md` |
| 黑格尔辩证法分析 | `dialectic` | `<书名>_黑格尔辩证法分析.md` |

### 信息图生成（infographic 子命令）

报告生成后，可为每个模型生成配套信息图（PNG）。

```bash
# 为所有已完成报告生成信息图（默认 sketch-note 风格）
.venv/bin/python -u batch_generate.py infographic

# 指定风格
.venv/bin/python -u batch_generate.py infographic --style scientific

# 只生成指定模型的信息图
.venv/bin/python -u batch_generate.py infographic --only axiom,bayes

# 覆盖已有文件
.venv/bin/python -u batch_generate.py infographic --force
```

### 信息图风格选项

`notebooklm generate infographic --style` 支持 11 种风格：

| 风格 | 说明 |
|------|------|
| `auto` | 自动选择 |
| `sketch-note` | 手绘草图（默认） |
| `professional` | 专业商务 |
| `bento-grid` | 便当盒网格布局 |
| `editorial` | 编辑排版风 |
| `instructional` | 教学说明风 |
| `bricks` | 积木拼贴风 |
| `clay` | 黏土风格 |
| `anime` | 动漫风格 |
| `kawaii` | 可爱风 |
| `scientific` | 科学图表风 |

信息图文件命名: `<书名>_<模型名>_信息图.png`，保存在电子书同目录。

## 数据流

1. `discover`: NotebookLM API → JSON 解析 → 扫描文件系统 → 模糊匹配 → 写入 SQLite
2. `generate`: SQLite 读取 pending 行 → 加载 YAML 模型 prompt → 子进程调用 `notebooklm generate report --format custom` → `artifact list` 获取 artifact_id → 下载 `.md` 报告 → 更新 SQLite 状态
3. `infographic`: SQLite 读取 done 行 → 使用各模型精简 prompt → 子进程调用 `notebooklm generate infographic --style <style>` → 下载 `.png` 信息图
4. `mindmap`: SQLite 读取 matched 行 → 子进程调用 `notebooklm generate mind-map`（同步，无需 --wait）→ 下载 `.json` 思维导图
5. `status`: SQLite 聚合查询 → 终端表格输出

## 完整产出清单（每本书）

| # | 产物 | 格式 | 子命令 |
|---|------|------|--------|
| 1-5 | 5 种视角分析报告 | `.md` | `generate` |
| 6-10 | 5 种视角信息图 | `.png` | `infographic` |
| 11 | 全书思维导图 | `.json` | `mindmap` |

## 状态机

每个模型状态: `pending` → `generating` → `done` | `failed`。`skipped` 为用户手动跳过。

## SQLite Schema

```sql
CREATE TABLE IF NOT EXISTS books (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    notebook_id TEXT NOT NULL UNIQUE,
    notebook_title TEXT NOT NULL,
    ebook_filename TEXT,
    ebook_path TEXT,
    matched INTEGER DEFAULT 0,
    prompt_key TEXT DEFAULT 'default',

    axiom_status TEXT DEFAULT 'pending',
    axiom_artifact_id TEXT,
    axiom_path TEXT,

    bayes_status TEXT DEFAULT 'pending',
    bayes_artifact_id TEXT,
    bayes_path TEXT,

    academic_status TEXT DEFAULT 'pending',
    academic_artifact_id TEXT,
    academic_path TEXT,

    first_principles_status TEXT DEFAULT 'pending',
    first_principles_artifact_id TEXT,
    first_principles_path TEXT,

    dialectic_status TEXT DEFAULT 'pending',
    dialectic_artifact_id TEXT,
    dialectic_path TEXT,

    error_log TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
```
