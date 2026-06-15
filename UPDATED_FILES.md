# 本次更新文件说明：books.db 兼容迁移版

这次是在上一版“按书名自适应选择 11 个阅读模型”的基础上，针对你补充上传的旧版 `books.db` 做了兼容调整。

## 新增 / 更新文件

### 1. `batch_generate.py`

更新内容：

- `init_db()` 会自动创建新版表：
  - `model_selections`
  - `model_runs`
  - `legacy_academic_runs`
- 自动把旧版 `books` 表里的 4 个旧模型状态迁移到 `model_runs`：
  - `axiom`
  - `bayes`
  - `first_principles`
  - `dialectic`
- 保留旧版 `academic` 记录到 `legacy_academic_runs`，但不映射到新版结构地图模型。
- `status` 命令会显示“历史已完成模型”，避免误判旧成果。

### 2. `migrate_books_db.py`

新增独立迁移脚本，可手动执行：

```bash
python3 migrate_books_db.py --db books.db
```

默认会自动备份旧数据库。

### 3. `README-BOOKS-DB-MIGRATION.md`

说明为什么旧版 `books.db` 需要迁移、迁移做了什么、如何执行、迁移后如何验证。

## 测试结果

已用你上传的 `books.db` 测试：

- 原库包含 35 本书
- 成功创建新版 `model_runs`
- 成功迁移 140 条旧模型运行记录
- 成功保存 35 条旧版 academic/学术拆解记录到 `legacy_academic_runs`
- `batch_generate.py status --book 大国大城` 可正常识别历史已完成模型

## 推荐使用

把更新包文件复制到你的本地项目目录后，先运行：

```bash
python3 batch_generate.py status
```

或者显式迁移：

```bash
python3 migrate_books_db.py --db books.db
```
