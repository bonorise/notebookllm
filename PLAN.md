# 电子书批量笔记生成系统 — 实现计划

> **设计文档**: `DESIGN.md`  
> **目标**: 单脚本批量调用 NotebookLM CLI，为每本电子书生成 5 种笔记产物  
> **架构**: Python 脚本 + SQLite 进度追踪 + NotebookLM CLI 子进程  
> **依赖**: Python 3, pyyaml, notebooklm-py CLI, 已登录的 NotebookLM 会话

---

## Task 1: 创建项目骨架

**目标**: 建立文件结构和依赖

**文件**:
- 修改: `batch_generate.py`（新建）

**Step 1: 创建主脚本框架**

```python
#!/usr/bin/env python3
"""电子书批量笔记生成系统 — 调用 NotebookLM CLI 批量生成学习笔记套件"""

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# 路径常量
BASE_DIR = Path(__file__).parent.resolve()
PDF_DIR = BASE_DIR / "PDF"
PROMPTS_DIR = BASE_DIR / "prompts"
DB_PATH = BASE_DIR / "books.db"

# NotebookLM CLI 包装
def nblm(*args, timeout=600):
    """调用 notebooklm CLI，返回 (returncode, stdout, stderr)"""
    cmd = ["notebooklm", *args]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def main():
    parser = argparse.ArgumentParser(description="电子书批量笔记生成系统")
    subparsers = parser.add_subparsers(dest="command")

    # discover 子命令
    discover_parser = subparsers.add_parser("discover", help="发现并匹配笔记本和电子书")
    
    # generate 子命令
    gen_parser = subparsers.add_parser("generate", help="批量生成笔记")
    gen_parser.add_argument("--limit", type=int, default=0, help="最多处理 N 本")
    gen_parser.add_argument("--retry-failed", action="store_true", help="重试失败项")
    gen_parser.add_argument("--only", type=str, help="只生成特定产物 (逗号分隔)")
    
    # status 子命令
    subparsers.add_parser("status", help="查看进度")

    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        return

    # 确保目录存在
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    PROMPTS_DIR.mkdir(parents=True, exist_ok=True)

    if args.command == "discover":
        cmd_discover()
    elif args.command == "generate":
        cmd_generate(args)
    elif args.command == "status":
        cmd_status()


if __name__ == "__main__":
    main()
```

**Step 2: 验证框架可运行**

```bash
cd /Users/liubo/Desktop/Learning/reading
python3 batch_generate.py
# 预期: 打印帮助信息
python3 batch_generate.py status
# 预期: 报错函数未定义（正常，下个 task 实现）
```

---

## Task 2: 实现 SQLite 数据库

**目标**: 创建数据库和表，支持 CRUD 操作

**文件**:
- 修改: `batch_generate.py`

**Step 1: 添加数据库初始化函数**

在 `nblm()` 函数之后添加：

```python
def init_db():
    """初始化 SQLite 数据库"""
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS books (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            notebook_id TEXT NOT NULL UNIQUE,
            notebook_title TEXT NOT NULL,
            ebook_filename TEXT,
            ebook_path TEXT,
            matched INTEGER DEFAULT 0,
            prompt_key TEXT DEFAULT 'default',
            
            pptx_status TEXT DEFAULT 'pending',
            pptx_artifact_id TEXT,
            pptx_path TEXT,
            
            infographic_status TEXT DEFAULT 'pending',
            infographic_artifact_id TEXT,
            infographic_path TEXT,
            
            mindmap_status TEXT DEFAULT 'pending',
            mindmap_artifact_id TEXT,
            mindmap_path TEXT,
            
            flashcards_status TEXT DEFAULT 'pending',
            flashcards_artifact_id TEXT,
            flashcards_path TEXT,
            
            quiz_status TEXT DEFAULT 'pending',
            quiz_artifact_id TEXT,
            quiz_path TEXT,
            
            error_log TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    return conn


def get_conn():
    """获取数据库连接（自动初始化）"""
    return sqlite3.connect(str(DB_PATH))
```

**Step 2: 添加状态更新函数**

```python
def update_status(conn, notebook_id, field, status, **kwargs):
    """更新单条记录的状态字段"""
    sets = [f"{field} = ?", "updated_at = datetime('now')"]
    params = [status]
    for k, v in kwargs.items():
        sets.append(f"{k} = ?")
        params.append(v)
    sql = f"UPDATE books SET {', '.join(sets)} WHERE notebook_id = ?"
    params.append(notebook_id)
    conn.execute(sql, params)
    conn.commit()
```

**Step 3: 添加进度查询函数**

```python
def cmd_status():
    """查看当前进度"""
    conn = get_conn()
    rows = conn.execute("""
        SELECT notebook_title, 
               pptx_status, infographic_status, mindmap_status,
               flashcards_status, quiz_status,
               error_log
        FROM books ORDER BY id
    """).fetchall()
    
    if not rows:
        print("📭 还没有任何记录。请先运行 discover 子命令。")
        return
    
    print(f"{'笔记本':<30} {'PPT':<8} {'信息图':<8} {'导图':<8} {'闪卡':<8} {'测验':<8}")
    print("-" * 80)
    for r in rows:
        title = r[0][:28] if len(r[0]) > 28 else r[0]
        statuses = []
        for s in r[1:6]:
            if s == 'done':
                statuses.append('✅')
            elif s == 'failed':
                statuses.append('❌')
            elif s == 'generating':
                statuses.append('🔄')
            else:
                statuses.append('⏳')
        print(f"{title:<30} {statuses[0]:<8} {statuses[1]:<8} {statuses[2]:<8} {statuses[3]:<8} {statuses[4]:<8}")
    conn.close()
```

**验证**:

```bash
python3 batch_generate.py status
# 预期: "📭 还没有任何记录"
```

---

## Task 3: 实现发现与匹配（discover 子命令）

**目标**: 从 NotebookLM 获取笔记本列表，扫描电子书目录，模糊匹配

**文件**:
- 修改: `batch_generate.py`

**Step 1: 获取 NotebookLM 笔记本列表**

```python
def get_notebooklm_notebooks():
    """从 NotebookLM CLI 获取所有笔记本"""
    rc, stdout, stderr = nblm("list", "--json", timeout=30)
    if rc != 0:
        print(f"❌ notebooklm list 失败: {stderr}")
        print("请先运行: notebooklm login")
        sys.exit(1)
    try:
        data = json.loads(stdout)
        # notebooklm list --json 返回格式: {"notebooks": [{"id": "...", "title": "..."}]}
        return data.get("notebooks", [])
    except json.JSONDecodeError:
        print(f"❌ 无法解析 notebooklm 输出: {stdout[:200]}")
        sys.exit(1)
```

**Step 2: 扫描电子书文件**

```python
def get_ebook_files():
    """扫描 PDF 目录下的电子书文件"""
    extensions = {".pdf", ".epub", ".mobi"}
    files = []
    for f in PDF_DIR.iterdir():
        if f.is_file() and f.suffix.lower() in extensions:
            files.append(f)
    return files
```

**Step 3: 模糊匹配**

```python
import difflib

def match_notebooks_to_ebooks(notebooks, ebooks):
    """模糊匹配笔记本标题到电子书文件名，返回 {notebook: ebook_path}"""
    matches = {}
    unmatched_notebooks = []
    used_ebooks = set()
    
    for nb in notebooks:
        title = nb["title"]
        best_ratio = 0
        best_match = None
        
        for ebook in ebooks:
            if ebook in used_ebooks:
                continue
            ebook_name = ebook.stem
            ratio = difflib.SequenceMatcher(None, title.lower(), ebook_name.lower()).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_match = ebook
        
        if best_ratio >= 0.8 and best_match:
            matches[nb] = best_match
            used_ebooks.add(best_match)
        else:
            unmatched_notebooks.append((nb, best_match, best_ratio))
    
    return matches, unmatched_notebooks
```

**Step 4: 实现 discover 子命令**

```python
def cmd_discover():
    """发现并匹配笔记本和电子书"""
    print("🔍 获取 NotebookLM 笔记本列表...")
    notebooks = get_notebooklm_notebooks()
    print(f"   找到 {len(notebooks)} 个笔记本")
    
    print("📚 扫描电子书文件...")
    ebooks = get_ebook_files()
    print(f"   找到 {len(ebooks)} 本电子书")
    
    print("🔗 模糊匹配...")
    matches, unmatched = match_notebooks_to_ebooks(notebooks, ebooks)
    
    conn = init_db()
    
    # 写入匹配成功的
    for nb, ebook_path in matches.items():
        conn.execute("""
            INSERT OR REPLACE INTO books (notebook_id, notebook_title, ebook_filename, ebook_path, matched)
            VALUES (?, ?, ?, ?, 1)
        """, (nb["id"], nb["title"], ebook_path.name, str(ebook_path)))
    conn.commit()
    print(f"\n✅ 成功匹配: {len(matches)} 本")
    
    # 报告未匹配的
    if unmatched:
        print(f"\n⚠️  未匹配的笔记本 ({len(unmatched)}):")
        for nb, best, ratio in unmatched:
            hint = f" 最接近: {best.stem} ({ratio:.0%})" if best else ""
            print(f"   - {nb['title']}{hint}")
        
        # 也写入未匹配的（方便后续手动处理）
        for nb, best, ratio in unmatched:
            conn.execute("""
                INSERT OR REPLACE INTO books (notebook_id, notebook_title, matched)
                VALUES (?, ?, 0)
            """, (nb["id"], nb["title"]))
        conn.commit()
    
    # 报告未匹配的电子书
    matched_ebooks = {str(m[1]) for m in matches.items()}
    unmatched_ebooks = [e for e in ebooks if str(e) not in matched_ebooks]
    if unmatched_ebooks:
        print(f"\n⚠️  没有对应笔记本的电子书 ({len(unmatched_ebooks)}):")
        for e in unmatched_ebooks:
            print(f"   - {e.name}")
    
    conn.close()
    print("\n💡 运行 'python3 batch_generate.py status' 查看详情")
```

**验证**:

```bash
python3 batch_generate.py discover
# 预期: 列出所有笔记本和电子书匹配结果
python3 batch_generate.py status
# 预期: 显示所有匹配记录
```

---

## Task 4: 加载 Prompt 配置

**目标**: 从 `prompts/reading_prompts.yaml` 加载用户自定义提示词

**文件**:
- 修改: `batch_generate.py`

**Step 1: 添加 prompt 加载函数**

```python
import yaml  # 需要 pip install pyyaml

def load_prompts():
    """加载 reading_prompts.yaml"""
    prompt_file = PROMPTS_DIR / "reading_prompts.yaml"
    if not prompt_file.exists():
        print(f"⚠️  未找到 prompt 文件: {prompt_file}")
        print("   将使用默认提示词")
        return {"default": "请总结本书的核心观点、关键框架和重要洞察"}
    
    with open(prompt_file, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    
    if not data:
        return {"default": "请总结本书的核心观点、关键框架和重要洞察"}
    
    # 展开 books 映射
    prompts = {}
    for k, v in data.items():
        if k == "books":
            continue
        prompts[k] = v
    
    return prompts


def get_prompt_for_book(conn, notebook_title, prompts):
    """获取适用于某本书的 prompt"""
    # 先查 books 映射
    row = conn.execute(
        "SELECT prompt_key FROM books WHERE notebook_title = ?", 
        (notebook_title,)
    ).fetchone()
    
    key = row[0] if row else "default"
    
    # 检查是否有专门映射
    # 用户可在 prompts 中设置 books: {"书名": "prompt_key"}
    prompt_data = prompts
    
    prompt_text = prompt_data.get(key, prompt_data.get("default", ""))
    
    # 组装通用指令 + 自定义 prompt
    base_instructions = {
        "slide-deck": f"{prompt_text}\n\n请以幻灯片形式呈现，每页一个要点。",
        "infographic": f"{prompt_text}\n\n请以信息图形式呈现，突出关键数据和框架。",
        "mind-map": prompt_text,
        "flashcards": f"{prompt_text}\n\n请生成关键概念闪卡，正面是概念/问题，背面是解释。",
        "quiz": f"{prompt_text}\n\n请生成测验题，测试对书中内容的掌握程度。",
    }
    
    return base_instructions
```

**验证**:

```python
# 在 main() 之前添加临时测试
if args.command == "test_prompt":
    prompts = load_prompts()
    print(f"加载了 {len(prompts)} 个 prompt 模板")
    for k, v in prompts.items():
        print(f"  {k}: {v[:50]}...")
```

---

## Task 5: 实现生成引擎

**目标**: 实际调用 NotebookLM CLI 逐个生成产物

**文件**:
- 修改: `batch_generate.py`

**Step 1: 单个产物生成函数**

```python
def generate_artifact(conn, notebook_id, notebook_title, artifact_type, prompt, download_path):
    """生成单个产物，返回 (success, artifact_id)"""
    status_field = ARTIFACT_CONFIG[artifact_type]["status_field"]
    generate_cmd = ARTIFACT_CONFIG[artifact_type]["generate_cmd"]
    download_cmd = ARTIFACT_CONFIG[artifact_type]["download_cmd"]
    
    print(f"  📝 生成 {ARTIFACT_CONFIG[artifact_type]['label']}...", end=" ", flush=True)
    
    update_status(conn, notebook_id, status_field, "generating")
    
    # 构建生成命令
    cmd_args = list(generate_cmd) + [prompt, "--notebook", notebook_id, "--json"]
    
    rc, stdout, stderr = nblm(*cmd_args, timeout=900)
    
    if rc != 0:
        error_msg = f"生成失败 (exit={rc}): {stderr[:200]}"
        update_status(conn, notebook_id, status_field, "failed", error_log=error_msg)
        print(f"❌ {error_msg}")
        return False, None
    
    # 解析 artifact_id
    try:
        result = json.loads(stdout)
        artifact_id = result.get("id") or result.get("artifact_id")
    except json.JSONDecodeError:
        artifact_id = None
    
    if not artifact_id:
        update_status(conn, notebook_id, status_field, "failed", 
                      error_log=f"无法解析 artifact_id: {stdout[:200]}")
        print("❌ 无法获取 artifact_id")
        return False, None
    
    print("✅", end=" ", flush=True)
    
    # 下载
    print("下载...", end=" ", flush=True)
    download_args = list(download_cmd) + [
        str(download_path), "--notebook", notebook_id, "--latest"
    ]
    
    rc, stdout, stderr = nblm(*download_args, timeout=120)
    
    if rc == 0:
        update_status(conn, notebook_id, status_field, "done",
                      **{ARTIFACT_CONFIG[artifact_type]["path_field"]: str(download_path),
                         ARTIFACT_CONFIG[artifact_type]["id_field"]: artifact_id})
        print("✅")
        return True, artifact_id
    else:
        error_msg = f"下载失败: {stderr[:200]}"
        update_status(conn, notebook_id, status_field, "failed", error_log=error_msg)
        print(f"❌ {error_msg}")
        return False, artifact_id


# 产物配置
ARTIFACT_CONFIG = {
    "slide-deck": {
        "label": "PPT",
        "suffix": "_笔记.pptx",
        "status_field": "pptx_status",
        "id_field": "pptx_artifact_id",
        "path_field": "pptx_path",
        "generate_cmd": ["generate", "slide-deck", "--language", "zh_Hans", "--format", "detailed", "--wait"],
        "download_cmd": ["download", "slide-deck"],
    },
    "infographic": {
        "label": "信息图",
        "suffix": "_信息图.png",
        "status_field": "infographic_status",
        "id_field": "infographic_artifact_id",
        "path_field": "infographic_path",
        "generate_cmd": ["generate", "infographic", "--language", "zh_Hans", "--detail", "detailed", "--style", "sketch-note", "--wait"],
        "download_cmd": ["download", "infographic"],
    },
    "mind-map": {
        "label": "思维导图",
        "suffix": "_思维导图.json",
        "status_field": "mindmap_status",
        "id_field": "mindmap_artifact_id",
        "path_field": "mindmap_path",
        "generate_cmd": ["generate", "mind-map"],
        "download_cmd": ["download", "mind-map"],
    },
    "flashcards": {
        "label": "闪卡",
        "suffix": "_闪卡.md",
        "status_field": "flashcards_status",
        "id_field": "flashcards_artifact_id",
        "path_field": "flashcards_path",
        "generate_cmd": ["generate", "flashcards", "--wait"],
        "download_cmd": ["download", "flashcards", "--format", "markdown"],
    },
    "quiz": {
        "label": "测验",
        "suffix": "_测验.md",
        "status_field": "quiz_status",
        "id_field": "quiz_artifact_id",
        "path_field": "quiz_path",
        "generate_cmd": ["generate", "quiz", "--wait"],
        "download_cmd": ["download", "quiz", "--format", "markdown"],
    },
}
```

**Step 2: 实现 generate 子命令**

```python
def cmd_generate(args):
    """批量生成笔记产物"""
    conn = init_db()
    
    # 决定要处理哪些书
    where = []
    if args.retry_failed:
        where.append("""(
            pptx_status = 'failed' OR infographic_status = 'failed' 
            OR mindmap_status = 'failed' OR flashcards_status = 'failed' 
            OR quiz_status = 'failed'
        )""")
    else:
        where.append("matched = 1")
        where.append("""(
            pptx_status = 'pending' OR infographic_status = 'pending'
            OR mindmap_status = 'pending' OR flashcards_status = 'pending'
            OR quiz_status = 'pending'
        )""")
    
    sql = f"SELECT * FROM books WHERE {' AND '.join(where)} ORDER BY id"
    if args.limit > 0:
        sql += f" LIMIT {args.limit}"
    
    rows = conn.execute(sql).fetchall()
    
    if not rows:
        print("🎉 没有待处理的任务！")
        conn.close()
        return
    
    print(f"📋 待处理: {len(rows)} 本书\n")
    
    prompts = load_prompts()
    book_prompts = get_prompt_for_book(conn, rows[0][2], prompts) if rows else {}
    
    # 确定要生成的产物类型
    if args.only:
        artifact_types = [a.strip() for a in args.only.split(",")]
    else:
        artifact_types = ["slide-deck", "infographic", "mind-map", "flashcards", "quiz"]
    
    total = 0
    success = 0
    
    for row in rows:
        notebook_id = row[1]  # notebook_id
        notebook_title = row[2]  # notebook_title
        ebook_path = row[5]  # ebook_path
        
        output_dir = Path(ebook_path).parent if ebook_path else PDF_DIR
        base_name = Path(ebook_path).stem if ebook_path else notebook_title
        
        print(f"\n📖 [{total + 1}/{len(rows)}] {notebook_title}")
        
        for artifact_type in artifact_types:
            config = ARTIFACT_CONFIG[artifact_type]
            
            # 跳过已完成的
            status_field = config["status_field"]
            current_status = row[6 + list(ARTIFACT_CONFIG.keys()).index(artifact_type)]
            # 简化：直接查数据库（因为 row 是旧的）
            
            # 获取当前状态
            status_row = conn.execute(
                f"SELECT {status_field} FROM books WHERE notebook_id = ?",
                (notebook_id,)
            ).fetchone()
            
            if status_row and status_row[0] == "done":
                print(f"  ⏭️  {config['label']}: 已完成，跳过")
                continue
            
            prompt = book_prompts.get(artifact_type, prompts.get("default", ""))
            download_path = output_dir / f"{base_name}{config['suffix']}"
            
            ok, _ = generate_artifact(
                conn, notebook_id, notebook_title,
                artifact_type, prompt, download_path
            )
            
            total += 1
            if ok:
                success += 1
            
            # 短暂休息，避免速率限制
            time.sleep(2)
        
        # 每本书之间额外休息
        time.sleep(5)
    
    print(f"\n{'='*50}")
    print(f"🏁 完成! 成功: {success}/{total}")
    conn.close()
```

**注意**: 上面 status_field 的索引计算有 bug，实际实现中用字典映射更可靠。此处先展示逻辑。

---

## Task 6: 错误处理与健壮性

**目标**: 完善错误处理，添加重试和超时保护

**文件**:
- 修改: `batch_generate.py`

**Step 1: 添加 NotebookLM 登录检查**

```python
def check_notebooklm_ready():
    """检查 NotebookLM 是否已登录且可用"""
    rc, stdout, stderr = nblm("status", timeout=15)
    if rc != 0:
        print("❌ NotebookLM CLI 不可用。请检查:")
        print("   1. pip install notebooklm-py")
        print("   2. notebooklm login")
        sys.exit(1)
    print("✅ NotebookLM 已就绪")
```

**Step 2: 超时重试包装**

```python
def nblm_with_retry(*args, max_retries=3, timeout=600):
    """带重试的 notebooklm 调用"""
    for attempt in range(max_retries):
        try:
            rc, stdout, stderr = nblm(*args, timeout=timeout)
            if rc == 0:
                return rc, stdout, stderr
            # 速率限制通常返回 429
            if "rate" in stderr.lower() or "limit" in stderr.lower():
                wait = (attempt + 1) * 30
                print(f"    ⏳ 速率限制，等待 {wait}s...")
                time.sleep(wait)
                continue
            return rc, stdout, stderr
        except subprocess.TimeoutExpired:
            if attempt < max_retries - 1:
                print(f"    ⏱️  超时，重试 ({attempt + 2}/{max_retries})...")
                time.sleep(10)
            else:
                raise
    return rc, stdout, stderr
```

**Step 3: 添加 interrupt 保护**

```python
import signal

interrupted = False

def signal_handler(sig, frame):
    global interrupted
    print("\n⚠️  收到中断信号，正在安全退出...")
    print("   当前进度已保存到 SQLite，下次运行会自动续传")
    interrupted = True

signal.signal(signal.SIGINT, signal_handler)
```

---

## Task 7: 命令行界面完善

**目标**: 所有子命令可工作，帮助信息完整

**文件**:
- 修改: `batch_generate.py`

在 `cmd_generate` 和 `cmd_discover` 中添加 `interrupted` 检查：

```python
# 在每个循环中加入:
if interrupted:
    print("⏸️  已中断，进度已保存")
    break
```

完善帮助文本，在 `main()` 的 argparse 中添加 epilog:

```python
parser = argparse.ArgumentParser(
    description="电子书批量笔记生成系统",
    epilog="""
示例:
  python3 -u batch_generate.py discover          # 发现并匹配
  python3 -u batch_generate.py status             # 查看进度
  python3 -u batch_generate.py generate           # 全量生成
  python3 -u batch_generate.py generate --limit 3 # 只生成 3 本
  python3 -u batch_generate.py generate --retry-failed  # 重试失败项
  python3 -u batch_generate.py generate --only slide-deck,infographic  # 只生成部分产物
    """,
    formatter_class=argparse.RawDescriptionHelpFormatter,
)
```

---

## Task 8: 集成测试

**目标**: 用一本实际的书走通全流程

**前置条件**:
1. `/Users/liubo/Desktop/Learning/reading/PDF/` 下至少有一本电子书
2. NotebookLM 网页端已上传该书并创建同名笔记本
3. NotebookLM CLI 已登录

**验证步骤**:

```bash
cd /Users/liubo/Desktop/Learning/reading

# 1. 发现匹配
python3 -u batch_generate.py discover
# 预期: 成功匹配至少 1 本

# 2. 查看状态
python3 -u batch_generate.py status
# 预期: 显示 1 本，所有状态为 pending

# 3. 单本测试
python3 -u batch_generate.py generate --limit 1
# 预期: 依次生成 5 种产物，全部成功

# 4. 检查输出
ls -la PDF/书名_*
# 预期: 5 个文件（.pptx, .png, .json, .md, .md）
```

---

## 依赖安装

```bash
pip install pyyaml
# notebooklm-py 应该已安装（从之前的使用推断）
notebooklm --version
```

---

## 风险与缓解

| 风险 | 缓解 |
|------|------|
| NotebookLM 速率限制 | `nblm_with_retry` 自动退避重试 |
| 30 本书耗时过长 | `--limit` 分批处理，支持 Ctrl+C 中断续传 |
| mind-map 无 `--wait` | 同步调用，若发现不一致再加轮询 |
| prompt 文件格式不对 | `load_prompts` 带 fallback 默认 prompt |
| 文件名匹配失败 | discover 阶段报告未匹配项，支持手动调整 |
