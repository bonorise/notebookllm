# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

电子书批量笔记生成系统 — 调用 NotebookLM CLI 为本地电子书用 5 种分析模型各生成全套内容。

每本书共 20 个产物：5 份 `.md` 分析报告 + 5 张 `.png` 信息图 + 5 个 `.pptx` 演示文稿 + 1 份 `.json` 思维导图 + 1 份闪卡 + 3 份测验。

**核心设计原则**: 产物不存入 SQLite（避免 schema 膨胀），用文件存在性做幂等检查（`output_path.exists()` + `--force` 覆盖）。报告生成粒度最细（每模型一列），slides/flashcards/quiz 通过文件系统去重。

- **项目目录**: 当前目录（`/Users/liubo/Desktop/PROJECT/00tools/notebookllm/`）
- **电子书目录**: `/Users/liubo/Desktop/Learning/reading/PDF/`（数据盘，每本书一个子目录）
- **设计文档**: `DESIGN.md`（原始设计，产物类型已变更）
- **实施计划**: `PLAN.md`（原始计划，子命令结构保留但产物逻辑已变更）
- **Prompt 模板源**: `reading_models/`（11 种分析框架的原始 prompt，`.md` 文件）
- **Prompt 配置**: `prompts/reading_prompts.yaml`（当前仅覆盖 5 个模型，待扩展）

## 阅读模型（Prompt 模板）

`reading_models/` 目录包含 11 种分析框架，作为 NotebookLM 生成时的 prompt 输入：

| 编号 | 模型 | 文件 | 核心用途 |
|------|------|------|----------|
| 01 | 公理化逻辑审计 | `model01-axiom-logic-audit.md` | 审查作者论证是否严密（提取假设→确立公理→还原推导→审视结论） |
| 02 | 贝叶斯认知更新 | `model02-bayesian-cognitive-update.md` | 判断读后应如何更新原有信念（先验→新证据→似然→后验） |
| 03 | 全书结构地图 | `model03-book-structure-map.md` | 看清一本书的整体骨架和章节功能 |
| 04 | 第一性原理重建 | `model04-first-principles-reconstruction.md` | 拆到底层事实后从零重建新框架 |
| 05 | 黑格尔辩证思辨 | `model05-hegelian-dialectics.md` | 正题→反题→合题，分析深层张力 |
| 06 | 全书结构地图(新版) | `model06-book-structure-map.md` | 快速建立全书知识地图，适合首轮阅读 |
| 07 | 因果机制 | `model07-causal-mechanism.md` | 分析变量、因果链和反馈回路 |
| 08 | 利益相关者与权力结构 | `model08-stakeholder-power-structure.md` | 谁受益、谁受损、谁有权、谁被忽视 |
| 09 | 概念工程 | `model09-concept-engineering.md` | 分析作者如何定义、改造和替换概念 |
| 10 | 实践转化 | `model10-practical-transformation.md` | 把书中思想转化为行动方案 |
| 11 | 盲区与反向阅读 | `model11-blindspot-reverse-reading.md` | 找出作者没说清楚、回避或简化的地方 |

> **注意**: 当前 `batch_generate.py` 仍使用 5 模型（`MODEL_CONFIG` 定义的前5个 + `prompts/reading_prompts.yaml`），尚未整合 06-11 模型。新增模型需同步更新 `MODEL_CONFIG`、`prompts/reading_prompts.yaml` 和 SQLite schema。

## 架构

单脚本架构：`batch_generate.py` + SQLite 进度追踪 + NotebookLM CLI 子进程调用。

```
batch_generate.py           # 唯一的主脚本（~1200 行）
  ├── discover 子命令        # 从 NotebookLM 获取笔记本 → 扫描电子书 → 模糊匹配
  ├── generate 子命令        # 逐本生成 5 份分析报告 (.md)，--wait 同步等待
  ├── infographic 子命令     # 为已完成报告生成 5 张信息图 (.png)，--wait 同步
  ├── mindmap 子命令         # 生成全书思维导图 (.json)，同步无需 --wait
  ├── slides 子命令          # 生成 5 个演示文稿 (.pptx)，--no-wait + 轮询（最长20分钟/个）
  ├── flashcards 子命令      # 生成关键概念闪卡 (.md)，--no-wait + 轮询
  ├── quiz 子命令            # 生成 3 难度测验 (.md)，--no-wait + 轮询（最长10分钟）
  └── status 子命令          # 从 SQLite 读取进度，表格化展示
books.db                     # SQLite（自动创建），记录每本书每个模型的生成状态
prompts/reading_prompts.yaml # 5 个分析模型的完整 prompt（已创建）
PDF/<书名>/                  # 每本书一个子目录，原书 + 所有产出物（ensure_output_dir 自动创建）
```

所有子命令都支持 `--book <书名>` 过滤和 `--limit <N>` 限制数量。

### 产物流程（每本书 5+ 步）

- **报告**: 按顺序生成：axiom → bayes → academic → first_principles → dialectic，使用 `--wait` 同步等待
- **信息图**: 依赖报告完成（SQLite 读 done 行），5 个模型各一张，使用 `--wait` 同步
- **演示文稿**: 依赖报告完成，每个模型用精简 prompt（`SLIDE_PROMPTS`），`--no-wait` + `poll_artifact_complete(max_wait=1200)` 轮询，`--format pptx` 下载
- **闪卡**: 全书一份，`--no-wait` + 轮询（`max_wait=600`），artifact type 为 `flashcard`
- **测验**: 3 个难度（easy/medium/hard）各一份，`--no-wait` + 轮询（`max_wait=600`），artifact type 为 `quiz`
- **思维导图**: 同步生成无需等待，artifact type 为 `mind-map`

### 关键设计决策

- **产物跟踪**: 报告用 SQLite 状态列追踪（`axiom_status` 等），slides/flashcards/quiz 用文件存在性做幂等（`output_path.exists()` → 跳过），避免 schema 膨胀
- **生成模式**: `generate`/`infographic` 用 `--wait` 阻塞等待；`slides`/`flashcards`/`quiz` 用 `--no-wait` + `poll_artifact_complete()` 轮询（通过对比 artifact list 前后数量判断完成）
- **产物类型映射**: `generate report --format custom`（报告）、`generate infographic`（信息图）、`generate slide-deck`（演示文稿）、`generate flashcards`（闪卡）、`generate quiz`（测验）、`generate mind-map`（思维导图）
- **匹配策略**: `difflib.SequenceMatcher` 模糊匹配，阈值 0.8（笔记本标题 vs 电子书文件名去扩展名）
- **中断续传**: SQLite 记录每步状态 + 文件存在性检查，Ctrl+C 安全中断后重新运行会跳过已完成的
- **速率限制**: 内置指数退避重试（`nblm_with_retry`），模型间 sleep 2s，书本间 sleep 5s
- **输出目录**: `ensure_output_dir(base_name)` 自动创建 `<书名>/` 子目录并将 PDF 移入
- **输出格式**: 报告 `.md`、信息图 `.png`、演示文稿 `.pptx`、思维导图 `.json`、闪卡 `.md`、测验 `.md`

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
.venv/bin/python -u batch_generate.py generate --book "大国大城"

# 生成信息图（需先完成报告生成）
.venv/bin/python -u batch_generate.py infographic
.venv/bin/python -u batch_generate.py infographic --style scientific
.venv/bin/python -u batch_generate.py infographic --only axiom
.venv/bin/python -u batch_generate.py infographic --force          # 覆盖已有文件

# 生成演示文稿（较慢，--no-wait + 轮询，每个约 10-20 分钟）
.venv/bin/python -u batch_generate.py slides
.venv/bin/python -u batch_generate.py slides --only axiom
.venv/bin/python -u batch_generate.py slides --force

# 生成闪卡
.venv/bin/python -u batch_generate.py flashcards
.venv/bin/python -u batch_generate.py flashcards --force

# 生成测验题（默认 3 个难度）
.venv/bin/python -u batch_generate.py quiz
.venv/bin/python -u batch_generate.py quiz --difficulty easy
.venv/bin/python -u batch_generate.py quiz --force

# 生成思维导图（需先完成 discover 匹配）
.venv/bin/python -u batch_generate.py mindmap
.venv/bin/python -u batch_generate.py mindmap --limit 1
.venv/bin/python -u batch_generate.py mindmap --force

# 对特定书籍生成全套内容
.venv/bin/python -u batch_generate.py generate --book "大国大城"
.venv/bin/python -u batch_generate.py infographic --book "大国大城"
.venv/bin/python -u batch_generate.py slides --book "大国大城"
.venv/bin/python -u batch_generate.py flashcards --book "大国大城"
.venv/bin/python -u batch_generate.py quiz --book "大国大城"
.venv/bin/python -u batch_generate.py mindmap --book "大国大城"

# 检查 NotebookLM 状态
notebooklm status
notebooklm list --json
```

### 模型简称与输出文件名

| 模型 | 简称 | 报告 | 信息图 | 演示文稿 |
|------|------|------|--------|----------|
| 公理体系分析 | `axiom` | `<书名>_公理体系分析.md` | `<书名>_公理体系分析_信息图.png` | `<书名>_公理体系分析_演示文稿.pptx` |
| 贝叶斯推理分析 | `bayes` | `<书名>_贝叶斯推理分析.md` | `<书名>_贝叶斯推理分析_信息图.png` | `<书名>_贝叶斯推理分析_演示文稿.pptx` |
| 学术拆解分析 | `academic` | `<书名>_学术拆解分析.md` | `<书名>_学术拆解分析_信息图.png` | `<书名>_学术拆解分析_演示文稿.pptx` |
| 第一性原理分析 | `first_principles` | `<书名>_第一性原理分析.md` | `<书名>_第一性原理分析_信息图.png` | `<书名>_第一性原理分析_演示文稿.pptx` |
| 黑格尔辩证法分析 | `dialectic` | `<书名>_黑格尔辩证法分析.md` | `<书名>_黑格尔辩证法分析_信息图.png` | `<书名>_黑格尔辩证法分析_演示文稿.pptx` |

其他产物文件命名：

| 产物 | 文件名 |
|------|--------|
| 思维导图 | `<书名>_思维导图.json` |
| 闪卡 | `<书名>_闪卡.md` |
| 测验 | `<书名>_测验_简单.md`、`<书名>_测验_中等.md`、`<书名>_测验_困难.md` |

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
2. `generate`: SQLite 读取 pending 行 → 加载 YAML 模型 prompt → 子进程调用 `notebooklm generate report --format custom` → `--wait` 等待 → 下载 `.md` 报告 → 更新 SQLite 状态
3. `infographic`: SQLite 读取 done 行 → 子进程调用 `notebooklm generate infographic --style <style>` → `--wait` 等待 → 下载 `.png` 信息图
4. `slides`: SQLite 读取 matched 行 → 子进程调用 `notebooklm generate slide-deck` → `--no-wait` → `poll_artifact_complete(max_wait=1200)` 轮询 → 下载 `.pptx` 演示文稿
5. `flashcards`: SQLite 读取 matched 行 → 文件存在性检查 → `notebooklm generate flashcards` → `--no-wait` → `poll_artifact_complete(max_wait=600)` 轮询 → 下载 `.md` 闪卡
6. `quiz`: SQLite 读取 matched 行 → 文件存在性检查 → `notebooklm generate quiz` → `--no-wait` → `poll_artifact_complete(max_wait=600)` 轮询 → 下载 `.md` 测验
7. `mindmap`: SQLite 读取 matched 行 → 子进程调用 `notebooklm generate mind-map`（同步，无需 --wait）→ 下载 `.json` 思维导图
8. `status`: SQLite 聚合查询 → 终端表格输出

**轮询机制** (`poll_artifact_complete`): 通过对比 `artifact list` 输出中指定 type 的 artifacts 数量是否增加来判断生成完成，每 10s 检查一次。演示文稿 max_wait=1200s（20分钟），闪卡/测验 max_wait=600s（10分钟）。

## 完整产出清单（每本书）

| # | 产物 | 格式 | 子命令 | 生成模式 |
|---|------|------|--------|----------|
| 1-5 | 5 种视角分析报告 | `.md` | `generate` | `--wait` 同步 |
| 6-10 | 5 种视角信息图 | `.png` | `infographic` | `--wait` 同步 |
| 11-15 | 5 种视角演示文稿 | `.pptx` | `slides` | `--no-wait` + 轮询 |
| 16 | 全书思维导图 | `.json` | `mindmap` | 同步 |
| 17 | 关键概念闪卡 | `.md` | `flashcards` | `--no-wait` + 轮询 |
| 18-20 | 测验（简/中/难） | `.md` | `quiz` | `--no-wait` + 轮询 |

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
