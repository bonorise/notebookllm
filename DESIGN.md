# 电子书批量笔记生成系统 — 设计文档

**日期**: 2026-05-27  
**作者**: 波波 + Hermes Agent  
**状态**: 设计完成，待 prompt 文件

---

## 1. 概述

为本地电子书批量生成学习笔记套件。用户将电子书上传至 NotebookLM 网页端（一本一个笔记本），脚本自动调用 NotebookLM CLI 为每本书生成 5 种产出物，保存到电子书同目录。

## 2. 输入

| 输入 | 来源 | 说明 |
|------|------|------|
| 电子书文件 | `/Users/liubo/Desktop/Learning/reading/PDF/` | PDF/EPUB 格式 |
| NotebookLM 笔记本 | 网页端手动上传创建 | 笔记本标题 = 电子书文件名（去扩展名） |
| 阅读 Prompt | `/Users/liubo/Desktop/Learning/reading/prompts/` | YAML 格式，支持多 prompt 映射 |

## 3. 产出（每本书 5 件套）

| # | 产物 | 格式 | 文件名 |
|---|------|------|--------|
| 1 | 要点总结 PPT | `.pptx` | `<书名>_笔记.pptx` |
| 2 | 知识信息图 | `.png` | `<书名>_信息图.png` |
| 3 | 思维导图 | `.json` | `<书名>_思维导图.json` |
| 4 | 闪卡 | `.md` | `<书名>_闪卡.md` |
| 5 | 测验 | `.md` | `<书名>_测验.md` |

全部保存在电子书同目录 `/Users/liubo/Desktop/Learning/reading/PDF/`。

## 4. NotebookLM CLI 命令详情

### 4.1 Slide Deck（要点总结 PPT）

```bash
notebooklm generate slide-deck "提示词内容" \
  --notebook <id> \
  --language zh_Hans \
  --format detailed \
  --wait \
  --json

notebooklm download slide-deck <输出路径>.pptx \
  --notebook <id> \
  --latest
```

### 4.2 Infographic（知识信息图）

```bash
notebooklm generate infographic "提示词内容" \
  --notebook <id> \
  --language zh_Hans \
  --detail detailed \
  --style sketch-note \
  --wait \
  --json

notebooklm download infographic <输出路径>.png \
  --notebook <id> \
  --latest
```

### 4.3 Mind Map（思维导图）

```bash
# 注意：mind-map 没有 --wait 选项，生成较快，同步等待即可
notebooklm generate mind-map \
  --notebook <id> \
  --json

notebooklm download mind-map <输出路径>.json \
  --notebook <id> \
  --latest
```

### 4.4 Flashcards（闪卡）

```bash
notebooklm generate flashcards "提示词内容" \
  --notebook <id> \
  --wait \
  --json

notebooklm download flashcards <输出路径>.md \
  --notebook <id> \
  --format markdown \
  --latest
```

### 4.5 Quiz（测验）

```bash
notebooklm generate quiz "提示词内容" \
  --notebook <id> \
  --wait \
  --json

notebooklm download quiz <输出路径>.md \
  --notebook <id> \
  --format markdown \
  --latest
```

## 5. 文件结构

```
/Users/liubo/Desktop/Learning/reading/
├── PDF/                              # 电子书 + 产出物
│   ├── 人类简史.pdf                   # 原始电子书
│   ├── 人类简史_笔记.pptx             # 生成: slide deck
│   ├── 人类简史_信息图.png            # 生成: infographic
│   ├── 人类简史_思维导图.json         # 生成: mind map
│   ├── 人类简史_闪卡.md               # 生成: flashcards
│   ├── 人类简史_测验.md               # 生成: quiz
│   └── ...
├── prompts/                          # 用户提供的阅读 prompt
│   └── reading_prompts.yaml          # key → prompt 映射
├── batch_generate.py                 # 主脚本
├── books.db                          # SQLite 进度数据库（自动创建）
└── DESIGN.md                         # 本文档
```

## 6. Prompt 文件格式（reading_prompts.yaml）

```yaml
# 用户自定义的阅读 prompt，key 用于匹配书籍
default: |
  你是一位深度阅读专家。请仔细阅读本书，从以下维度总结：
  1. 核心论点与框架
  2. 关键概念和定义
  3. 重要案例与数据
  4. 作者的核心洞察
  请用中文输出。

deep_reading: |
  （另一种风格的 prompt）

# 可以为特定书籍指定特定 prompt
books:
  "人类简史": "deep_reading"
  "思考快与慢": "default"
```

## 7. SQLite 进度追踪

### 7.1 Schema

```sql
CREATE TABLE IF NOT EXISTS books (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    
    -- 笔记本信息
    notebook_id TEXT NOT NULL UNIQUE,
    notebook_title TEXT NOT NULL,
    
    -- 匹配的电子书
    ebook_filename TEXT,
    ebook_path TEXT,
    matched BOOLEAN DEFAULT 0,
    
    -- Prompt 映射
    prompt_key TEXT DEFAULT 'default',
    
    -- Slide Deck
    pptx_status TEXT DEFAULT 'pending',
    pptx_artifact_id TEXT,
    pptx_path TEXT,
    
    -- Infographic
    infographic_status TEXT DEFAULT 'pending',
    infographic_artifact_id TEXT,
    infographic_path TEXT,
    
    -- Mind Map
    mindmap_status TEXT DEFAULT 'pending',
    mindmap_artifact_id TEXT,
    mindmap_path TEXT,
    
    -- Flashcards
    flashcards_status TEXT DEFAULT 'pending',
    flashcards_artifact_id TEXT,
    flashcards_path TEXT,
    
    -- Quiz
    quiz_status TEXT DEFAULT 'pending',
    quiz_artifact_id TEXT,
    quiz_path TEXT,
    
    -- 元数据
    error_log TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
```

### 7.2 状态值

| 状态 | 含义 |
|------|------|
| `pending` | 待处理 |
| `generating` | 生成中 |
| `done` | 已完成 |
| `failed` | 失败（见 error_log） |
| `skipped` | 手动跳过 |

## 8. 主脚本逻辑（batch_generate.py）

### 8.1 Phase 1: 发现与匹配

```
1. notebooklm list --json → 获取所有笔记本 {id, title}
2. 扫描 PDF/ 目录 → 获取电子书文件名
3. 模糊匹配:
   - 笔记本标题 vs 文件名去扩展名
   - 使用 difflib.SequenceMatcher，阈值 0.8
   - 匹配成功的写入 SQLite
   - 未匹配的打印列表，等待用户手动确认或跳过
```

### 8.2 Phase 2: 批量生成

```
对于每本状态为 pending 的书:
  1. 读取 prompt（根据 prompt_key 从 YAML 加载）
  2. 依次生成 5 种产物:
     a. slide-deck (--wait, 最长等待 10 分钟)
     b. infographic (--wait, 最长等待 5 分钟)
     c. mind-map (同步，无 --wait)
     d. flashcards (--wait, 最长等待 3 分钟)
     e. quiz (--wait, 最长等待 3 分钟)
  3. 每步完成后立即 commit 到 SQLite
  4. 失败时记录 error_log，继续下一本
```

### 8.3 命令行接口

```bash
# 全量生成
python3 -u batch_generate.py

# 只生成前 N 本
python3 -u batch_generate.py --limit 5

# 查看进度
python3 -u batch_generate.py --status

# 重新处理失败项
python3 -u batch_generate.py --retry-failed

# 只生成特定产物
python3 -u batch_generate.py --only slide-deck,infographic

# 跳过发现阶段，直接生成（已匹配过）
python3 -u batch_generate.py --skip-discovery
```

## 9. 错误处理

| 场景 | 处理方式 |
|------|----------|
| NotebookLM 未登录 | 提示用户执行 `notebooklm login`，退出 |
| 笔记本无法匹配 | 打印未匹配列表，继续处理已匹配的 |
| 生成超时 | 标记 failed，记录超时信息，继续下一本 |
| 生成失败（API 错误） | 标记 failed，记录错误信息，继续下一本 |
| 下载失败 | 标记 failed，记录错误信息 |
| 网络中断 | SQLite 已 commit 的不会丢失，重启后接续 |
| 速率限制 | `--retry` 参数自动重试（指数退避） |

## 10. 约束与风险

| 项目 | 说明 |
|------|------|
| NotebookLM 速率限制 | 连续生成可能触发限制，脚本内置 `--retry` 和退避 |
| 生成时间 | 30本书 × ~3分钟/本 ≈ 1.5-2.5 小时（乐观），建议后台运行 |
| 磁盘空间 | 每个 PPTX 约 1-5MB，PNG 约 1-3MB，总量 < 500MB |
| mind-map 无 --wait | 生成是同步的，但若返回速度过快可能需要短暂 sleep |
| PPTX vs PDF | Slide deck 下载为 PPTX，用户可自行在 PowerPoint/Keynote 中导出 PDF |

## 11. 依赖

```bash
# NotebookLM CLI
pip install notebooklm-py

# Python 标准库即可，额外仅需：
pip install pyyaml  # 解析 prompt YAML

# 需要先登录
notebooklm login
```

## 12. 后续扩展

- 支持生成音频概览 (podcast)
- 思维导图 JSON → 可视化图片（使用 Python graphviz/mermaid）
- 支持自定义每本书的 artifact 组合
- Web UI 查看进度

---

## 附录：匹配策略示例

```python
import difflib
from pathlib import Path

def match_notebook_to_ebook(notebook_title: str, ebook_files: list[Path]) -> Path | None:
    """模糊匹配笔记本标题到电子书文件名"""
    best_ratio = 0
    best_match = None
    
    for f in ebook_files:
        ebook_name = f.stem  # 去掉扩展名
        ratio = difflib.SequenceMatcher(None, notebook_title, ebook_name).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_match = f
    
    return best_match if best_ratio >= 0.8 else None
```

阈值 0.8 意味着允许轻微差异（如 "人类简史" ↔ "人类简史（新版）"），但不会误匹配完全不同的书。
