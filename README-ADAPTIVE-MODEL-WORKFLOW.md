# NotebookLM 电子书分析：按书名自适应选择阅读模型工作流

这版更新把原来的“每本书固定跑 5 个模型”改成：

```text
输入书名 / NotebookLM 笔记本标题
↓
搜索书籍公开资料（可选）
↓
调用 DeepSeek API 判断书籍类型与分析需求（可选）
↓
从 11 个阅读模型中选择 3-4 个最合适的模型
↓
只按已选模型生成报告、信息图、PPT
↓
再生成全书思维导图、闪卡、测验等通用产物
```

这样可以避免一次性把 11 个模型全部跑完，减少成本、时间和重复内容。

---

## 一、更新文件

本次更新的核心文件：

```text
batch_generate.py                         # 主脚本，已改为自适应模型选择流程
book_model_selector.py                    # 新增：书名搜索 + DeepSeek/启发式模型选择器
prompts/model_selection.yaml              # 新增：11 个模型的元信息、选择策略、信息图/PPT prompt
README-ADAPTIVE-MODEL-WORKFLOW.md         # 本说明文档
CLAUDE.md                                 # 已追加 Claude Code 使用说明
```

原来的 `reading_models/` 目录继续使用，里面保存 11 个模型的完整 Prompt。

---

## 二、环境变量

### 1. PDF 目录

默认仍然是：

```bash
/Users/liubo/Desktop/Learning/reading/PDF
```

也可以临时覆盖：

```bash
export PDF_DIR="/Users/liubo/Desktop/Learning/reading/PDF"
```

### 2. NotebookLM CLI

默认调用：

```bash
notebooklm
```

如果你的 CLI 路径不同，可以设置：

```bash
export NOTEBOOKLM_BIN="notebooklm"
```

### 3. DeepSeek API

如果希望模型选择更智能，建议配置 DeepSeek：

```bash
export DEEPSEEK_API_KEY="你的 deepseek key"
export DEEPSEEK_BASE_URL="https://api.deepseek.com"
export DEEPSEEK_MODEL="deepseek-chat"
```

如果不配置 DeepSeek，脚本会自动使用本地启发式规则兜底。

### 4. 搜索服务，可选

如果希望“根据书名搜索资料”更稳定，可以配置以下任一搜索服务：

```bash
# 推荐其一即可
export BOOK_SEARCH_PROVIDER="serper"
export SERPER_API_KEY="你的 serper key"
```

或：

```bash
export BOOK_SEARCH_PROVIDER="tavily"
export TAVILY_API_KEY="你的 tavily key"
```

或：

```bash
export BOOK_SEARCH_PROVIDER="brave"
export BRAVE_SEARCH_API_KEY="你的 brave key"
```

如果都不配置，会尝试 DuckDuckGo HTML 检索；如果网络失败，也会自动降级为书名启发式判断。

---

## 三、推荐使用流程

### 第 1 步：发现并匹配 NotebookLM 笔记本与本地电子书

```bash
cd /Users/liubo/Desktop/PROJECT/00tools/notebookllm
.venv/bin/python -u batch_generate.py discover
```

### 第 2 步：根据书名选择合适模型

```bash
.venv/bin/python -u batch_generate.py select-models --book "置身事内"
```

输出会类似：

```text
推荐模型:
- structure_quick: 全书知识地图
- causal: 因果机制分析
- power: 利益权力结构分析
- blindspot: 盲区反向阅读
```

这一步会把模型选择结果保存到 SQLite 的 `model_selections` 表，并为这些模型建立 `model_runs` 任务记录。

### 第 3 步：按已选模型生成分析报告

```bash
.venv/bin/python -u batch_generate.py generate --book "置身事内"
```

它不会再默认跑全部模型，而是读取第 2 步保存的模型选择结果。

如果还没有提前运行 `select-models`，`generate` 会自动选择模型。

### 第 4 步：按已选模型生成信息图

```bash
.venv/bin/python -u batch_generate.py infographic --book "置身事内"
```

可指定风格：

```bash
.venv/bin/python -u batch_generate.py infographic --book "置身事内" --style scientific
```

### 第 5 步：按已选模型生成 PPT

```bash
.venv/bin/python -u batch_generate.py slides --book "置身事内"
```

PPT 仍然使用 NotebookLM 的 `slide-deck` 能力，脚本会轮询等待完成。

### 第 6 步：生成通用产物

这些产物不依赖某个具体阅读模型，通常每本书只生成一份：

```bash
.venv/bin/python -u batch_generate.py mindmap --book "置身事内"
.venv/bin/python -u batch_generate.py flashcards --book "置身事内"
.venv/bin/python -u batch_generate.py quiz --book "置身事内"
```

### 第 7 步：查看状态

```bash
.venv/bin/python -u batch_generate.py status --book "置身事内"
```

---

## 四、单独测试“书名 → 模型选择”

不依赖 NotebookLM，只测试模型推荐：

```bash
.venv/bin/python -u batch_generate.py recommend --title "置身事内"
```

如果你暂时不想联网、不想调用 DeepSeek：

```bash
.venv/bin/python -u batch_generate.py recommend --title "置身事内" --no-search --no-llm --force
```

输出完整 JSON：

```bash
.venv/bin/python -u batch_generate.py recommend --title "置身事内" --json
```

---

## 五、模型选择逻辑

默认策略：

1. 不一次性选择全部 11 个模型。
2. 默认选择 3-4 个模型。
3. 通常包含 `structure_quick`，先建立全书知识地图。
4. 根据书籍类型追加 2-3 个模型。

### 常见类型与推荐模型

| 书籍类型 | 推荐模型 |
|---|---|
| 社会、经济、制度、城市、历史 | `structure_quick` + `causal` + `power` + `blindspot` |
| 商业、组织、管理 | `structure_quick` + `causal` + `concept` + `practice` |
| 个人成长、方法论、学习 | `structure_quick` + `concept` + `practice` + `bayes` |
| 哲学、思想、价值冲突 | `structure_quick` + `concept` + `dialectic` + `blindspot` |
| 科学、心理、认知、反常识 | `structure_quick` + `bayes` + `concept` + `practice` |
| 技术、创新、战略 | `structure_quick` + `first_principles` + `causal` + `practice` |
| 争议性强、观点强烈 | `structure_quick` + `axiom` + `blindspot` + `dialectic` |

---

## 六、11 个模型 key

| key | 模型 |
|---|---|
| `axiom` | 公理化逻辑审计 |
| `bayes` | 贝叶斯认知更新 |
| `structure` | 全书结构地图 |
| `first_principles` | 第一性原理重建 |
| `dialectic` | 黑格尔辩证思辨 |
| `structure_quick` | 全书知识地图 |
| `causal` | 因果机制分析 |
| `power` | 利益权力结构分析 |
| `concept` | 概念工程分析 |
| `practice` | 实践转化方案 |
| `blindspot` | 盲区反向阅读 |

---

## 七、手动指定模型

如果你想覆盖自动选择结果，可以使用：

```bash
.venv/bin/python -u batch_generate.py generate --book "置身事内" --only structure_quick,causal,power,blindspot
```

信息图和 PPT 同样支持：

```bash
.venv/bin/python -u batch_generate.py infographic --book "置身事内" --only causal,power
.venv/bin/python -u batch_generate.py slides --book "置身事内" --only causal,power
```

如果你确实想跑全部模型：

```bash
.venv/bin/python -u batch_generate.py generate --book "置身事内" --all-models
```

不推荐日常使用，因为耗时、成本高、输出容易重复。

---

## 八、SQLite 新增表

本次新增两张表：

### model_selections

记录每本书最终选择了哪些模型。

```sql
notebook_id
notebook_title
selected_models
book_profile
search_data
rationale
source
```

### model_runs

记录每本书每个模型的生成状态。

```sql
notebook_id
model_key
status
artifact_id
output_path
error_log
```

这样以后新增模型时，不再需要给 `books` 表增加大量固定列。

---

## 九、和旧流程的区别

旧流程：

```text
每本书 → 固定 5 个模型 → 5 份报告 → 5 张信息图 → 5 个 PPT
```

新流程：

```text
每本书 → 根据书名搜索与判断 → 选择 3-4 个模型 → 生成对应报告 / 信息图 / PPT
```

核心变化：

- 从固定模型改为自适应模型。
- 从 5 模型扩展到 11 模型，但默认不全跑。
- 从 SQLite 固定状态列改为 `model_runs` 动态记录。
- `generate`、`infographic`、`slides` 都默认按“已选模型”执行。

---

## 十、建议给 Claude Code 的执行 Prompt

```text
请在当前项目中应用这次“按书名自适应选择阅读模型”的更新：

1. 用新版 batch_generate.py 替换原主脚本。
2. 新增 book_model_selector.py。
3. 新增 prompts/model_selection.yaml。
4. 保留 reading_models/ 目录下 11 个模型 prompt。
5. 运行 python3 -m py_compile batch_generate.py book_model_selector.py 检查语法。
6. 用以下命令测试书名推荐：
   .venv/bin/python -u batch_generate.py recommend --title "置身事内" --no-search --no-llm --force
7. 确认可以输出推荐模型后，再运行：
   .venv/bin/python -u batch_generate.py status

注意：不要一次性全跑 11 个模型。默认流程必须是先 select-models，再 generate。
```
