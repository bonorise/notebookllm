# books.db 是否需要调整？

需要做一次兼容迁移，但**不建议手动改 SQLite 字段**。

你上传的 `books.db` 是旧版结构：

- 只有 `books` 主表
- 旧 5 个模型的状态直接存在 `books` 表里：
  - `axiom_status / axiom_artifact_id / axiom_path`
  - `bayes_status / bayes_artifact_id / bayes_path`
  - `academic_status / academic_artifact_id / academic_path`
  - `first_principles_status / first_principles_artifact_id / first_principles_path`
  - `dialectic_status / dialectic_artifact_id / dialectic_path`

新版 11 模型自适应流程改成：

- `model_selections`：保存某本书被推荐/选择了哪些模型
- `model_runs`：保存每本书、每个模型的生成状态和输出路径
- `legacy_academic_runs`：保存旧版 academic/学术拆解模型记录

这样做的原因是：11 个模型不能继续无限往 `books` 表里加 33 个字段，否则后续维护很麻烦。

---

## 一、这次具体怎么兼容旧数据库？

### 1. 不删除旧字段

旧的 `books` 表会保留，原来的字段和数据不会被删除。

### 2. 自动新增三张表

第一次运行新版 `batch_generate.py` 时，会自动创建：

```sql
model_selections
model_runs
legacy_academic_runs
```

### 3. 自动迁移旧 4 个模型状态

以下旧模型会自动迁移到 `model_runs`：

| 旧字段前缀 | 新模型 key |
|---|---|
| `axiom_*` | `axiom` |
| `bayes_*` | `bayes` |
| `first_principles_*` | `first_principles` |
| `dialectic_*` | `dialectic` |

如果旧库里这些模型已经是 `done`，新版就不会误以为它们还没生成。

### 4. academic 不自动映射到新版 structure

旧版的 `academic_status` 对应的是原来的“学术拆解 / 新旧范式”模型；新版的 `model03 / model06` 是“全书结构地图”。二者不完全等价。

因此迁移脚本会把旧 `academic_*` 保存到：

```sql
legacy_academic_runs
```

但不会自动把它标记成 `structure` 或 `structure_quick` 已完成，避免新流程误判。

---

## 二、推荐迁移方式

### 方式 A：直接使用新版 batch_generate.py 自动迁移

把本次更新包里的文件覆盖到你的项目目录后，运行：

```bash
python3 batch_generate.py status
```

它会自动：

1. 创建新表
2. 迁移旧模型状态
3. 显示历史已完成模型

---

### 方式 B：手动执行迁移脚本

如果你希望更明确地执行迁移，可以运行：

```bash
python3 migrate_books_db.py --db books.db
```

脚本会先自动备份：

```text
books.db.bak-YYYYMMDD-HHMMSS
```

然后执行迁移。

如果你已经自己备份过，也可以：

```bash
python3 migrate_books_db.py --db books.db --no-backup
```

---

## 三、迁移后应该看到什么？

运行：

```bash
python3 batch_generate.py status --book "大国大城"
```

如果这本书旧版已经生成过报告，你会看到类似：

```text
✅ 大国大城
   文件: 大国大城.pdf
   已选模型: 未选择（运行 select-models 或 generate 时自动选择）
   历史已完成模型: axiom, bayes, dialectic, first_principles
```

这表示旧状态已经被新版识别。

---

## 四、后续流程

迁移后，新流程建议这样跑：

```bash
python3 batch_generate.py status --book "置身事内"
python3 batch_generate.py select-models --book "置身事内"
python3 batch_generate.py generate --book "置身事内"
python3 batch_generate.py infographic --book "置身事内"
python3 batch_generate.py slides --book "置身事内"
```

如果某些旧模型已经完成，而本次自动选择又选中了它们，新版会跳过已完成项。

如果本次选择了新版新增模型，例如：

- `structure_quick`
- `causal`
- `power`
- `concept`
- `practice`
- `blindspot`

则只会生成这些新增模型对应的新报告。

---

## 五、重要提醒

1. 不要手动删除旧 `books` 表字段。
2. 不要把旧 `academic` 强行当作新版 `structure_quick`。
3. 迁移前最好保留备份。
4. 新版 `model_runs` 是未来主要的状态记录表。
5. 新版 `model_selections` 是未来按书名适配模型的核心记录表。
