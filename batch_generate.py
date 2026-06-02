#!/usr/bin/env python3
"""电子书批量笔记生成系统 — 用 5 种分析模型为每本电子书生成深度分析报告"""

import argparse
import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import yaml

# 路径常量
BASE_DIR = Path(__file__).parent.resolve()
PDF_DIR = Path("/Users/liubo/Desktop/Learning/reading/PDF")  # 电子书目录（数据盘）
PROMPTS_DIR = BASE_DIR / "prompts"
DB_PATH = BASE_DIR / "books.db"
PROMPT_FILE = PROMPTS_DIR / "reading_prompts.yaml"

# 中断信号处理
interrupted = False

def signal_handler(sig, frame):
    global interrupted
    print("\n⚠️  收到中断信号，正在安全退出...")
    print("   当前进度已保存到 SQLite，下次运行会自动续传")
    interrupted = True

signal.signal(signal.SIGINT, signal_handler)

# ============================================================
# NotebookLM CLI 包装
# ============================================================

def nblm(*args, timeout=600):
    """调用 notebooklm CLI，返回 (returncode, stdout, stderr)"""
    cmd = ["notebooklm", *args]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def nblm_with_retry(*args, max_retries=3, timeout=900):
    """带重试的 notebooklm 调用，遇到速率限制自动退避"""
    for attempt in range(max_retries):
        try:
            rc, stdout, stderr = nblm(*args, timeout=timeout)
            if rc == 0:
                return rc, stdout, stderr
            if "rate" in stderr.lower() or "limit" in stderr.lower() or "RATE_LIMITED" in stdout:
                wait = (attempt + 1) * 300
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


def check_notebooklm_ready():
    """检查 NotebookLM 是否已登录且可用"""
    rc, stdout, stderr = nblm("status", timeout=15)
    if rc != 0:
        print("❌ NotebookLM CLI 不可用。请检查:")
        print("   1. pip install notebooklm-py")
        print("   2. notebooklm login")
        sys.exit(1)
    print("✅ NotebookLM 已就绪")


# ============================================================
# 数据库
# ============================================================

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
        )
    """)
    conn.commit()
    return conn


def get_conn():
    """获取数据库连接"""
    return sqlite3.connect(str(DB_PATH))


def where_book(book_name):
    """生成按书名过滤的 WHERE 子句和参数"""
    if book_name:
        return " AND notebook_title LIKE ?", [f"%{book_name}%"]
    return "", []


def update_status(conn, notebook_id, status_field, status, **kwargs):
    """更新单条记录的状态字段"""
    sets = [f"{status_field} = ?", "updated_at = datetime('now')"]
    params = [status]
    for k, v in kwargs.items():
        sets.append(f"{k} = ?")
        params.append(v)
    sql = f"UPDATE books SET {', '.join(sets)} WHERE notebook_id = ?"
    params.append(notebook_id)
    conn.execute(sql, params)
    conn.commit()


# ============================================================
# 模型配置
# ============================================================

MODEL_CONFIG = {
    "axiom": {
        "label": "公理体系分析",
        "suffix": "_公理体系分析.md",
        "status_field": "axiom_status",
        "id_field": "axiom_artifact_id",
        "path_field": "axiom_path",
    },
    "bayes": {
        "label": "贝叶斯推理分析",
        "suffix": "_贝叶斯推理分析.md",
        "status_field": "bayes_status",
        "id_field": "bayes_artifact_id",
        "path_field": "bayes_path",
    },
    "academic": {
        "label": "学术拆解分析",
        "suffix": "_学术拆解分析.md",
        "status_field": "academic_status",
        "id_field": "academic_artifact_id",
        "path_field": "academic_path",
    },
    "first_principles": {
        "label": "第一性原理分析",
        "suffix": "_第一性原理分析.md",
        "status_field": "first_principles_status",
        "id_field": "first_principles_artifact_id",
        "path_field": "first_principles_path",
    },
    "dialectic": {
        "label": "黑格尔辩证法分析",
        "suffix": "_黑格尔辩证法分析.md",
        "status_field": "dialectic_status",
        "id_field": "dialectic_artifact_id",
        "path_field": "dialectic_path",
    },
}


# 信息图风格选项
INFOGRAPHIC_STYLES = [
    "auto", "sketch-note", "professional", "bento-grid", "editorial",
    "instructional", "bricks", "clay", "anime", "kawaii", "scientific",
]

# 各模型对应的信息图 prompt（精简版，用于 infographic 生成）
INFOGRAPHIC_PROMPTS = {
    "axiom": "基于公理体系分析视角，提取本书核心假设、公理命题和逻辑推导链。展示从初始假设到最终结论的推理路径和关键转折点。信息图，中文，手绘草图风格。",
    "bayes": "基于贝叶斯推理分析视角，展示本书如何用新证据刷新旧认知。呈现传统共识（先验）→异常现象→作者新观点（新证据）→认知更新（后验）的完整链条。信息图，中文，手绘草图风格。",
    "academic": "基于学术拆解分析视角，展示本书的核心问题→旧范式缺陷→新主张→新旧对照→论证路径→延伸推论框架。以结构化对比图呈现新旧范式的差异。信息图，中文，手绘草图风格。",
    "first_principles": "基于第一性原理分析视角，展示本书核心命题如何从不可再分的基本事实重新推导。用层级结构呈现既有假设→基本事实→重建结论的过程。信息图，中文，手绘草图风格。",
    "dialectic": "基于黑格尔辩证法分析视角，展示本书核心观点中的对立统一关系。以正反合三段式结构呈现关键论断的正题、反题和合题。信息图，中文，手绘草图风格。",
}


# 各模型对应的演示文稿 prompt（精简版，用于 slide-deck 生成）
SLIDE_PROMPTS = {
    "axiom": "基于公理体系（Axiomatic System）分析视角，创建演示文稿。从核心假设、基本公理出发，逐步展开逻辑推导过程，最终呈现关键结论。",
    "bayes": "基于贝叶斯推理分析视角，创建演示文稿。以先验信念、异常现象、新证据、认知更新为主线，展示作者如何刷新读者认知。",
    "academic": "基于学术拆解分析视角，创建演示文稿。以核心问题、旧范式缺陷、新主张、新旧对照、论证路径为框架，清晰对比新旧范式的差异。",
    "first_principles": "基于第一性原理（First Principles Thinking）视角，创建演示文稿。逐层剥开既有假设，回归不可再分的基本事实，再从零重建结论。",
    "dialectic": "基于黑格尔辩证法视角，创建演示文稿。以正题、反题、合题三段式结构呈现书中核心论断的对立统一关系。",
}


# 闪卡 prompt
FLASHCARD_PROMPT = "请为本书生成关键概念闪卡，正面是概念/术语/问题，背面是解释/定义/答案。涵盖全书最重要的概念、理论和框架。用中文。"

# 测验 prompt（按难度）
QUIZ_PROMPTS = {
    "easy": "请为本书生成一份简单难度的测验题，测试读者对书中基本概念和核心观点的理解。题型以选择和判断为主。用中文。",
    "medium": "请为本书生成一份中等难度的测验题，测试读者对书中理论框架、关键论证和概念之间关系的理解。包含选择、填空和简答。用中文。",
    "hard": "请为本书生成一份困难难度的测验题，测试读者对书中深层逻辑、批判性分析和跨章节综合的掌握。包含分析题和论述题。用中文。",
}


def load_prompts():
    """加载 reading_prompts.yaml，返回 {'default_models': [...], 'models': {...}, 'books': {...}}"""
    if not PROMPT_FILE.exists():
        print(f"⚠️  未找到 prompt 文件: {PROMPT_FILE}")
        sys.exit(1)

    with open(PROMPT_FILE, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not data or "models" not in data:
        print("❌ reading_prompts.yaml 缺少 models 配置")
        sys.exit(1)

    return {
        "default_models": data.get("default_models", list(data["models"].keys())),
        "models": data["models"],
        "books": data.get("books", {}),
    }


def get_models_for_book(notebook_title, prompts_config):
    """返回某本书应使用的模型列表"""
    books_map = prompts_config.get("books", {})
    if notebook_title in books_map:
        return books_map[notebook_title]
    return prompts_config["default_models"]


# ============================================================
# 发现与匹配
# ============================================================

def get_notebooklm_notebooks():
    """从 NotebookLM CLI 获取所有笔记本"""
    rc, stdout, stderr = nblm("list", "--json", timeout=30)
    if rc != 0:
        print(f"❌ notebooklm list 失败: {stderr}")
        print("请先运行: notebooklm login")
        sys.exit(1)
    try:
        data = json.loads(stdout)
        return data.get("notebooks", [])
    except json.JSONDecodeError:
        print(f"❌ 无法解析 notebooklm 输出: {stdout[:200]}")
        sys.exit(1)


def get_ebook_files():
    """扫描 PDF 目录下的电子书文件（含子目录）"""
    extensions = {".pdf", ".epub", ".mobi"}
    files = []
    if not PDF_DIR.exists():
        print(f"⚠️  PDF 目录不存在: {PDF_DIR}")
        return files
    for f in PDF_DIR.rglob("*"):
        if f.is_file() and f.suffix.lower() in extensions:
            files.append(f)
    return files


def match_notebooks_to_ebooks(notebooks, ebooks):
    """模糊匹配笔记本标题到电子书文件名，返回 {notebook_id: (notebook, ebook_path)}"""
    import difflib

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
            matches[nb["id"]] = (nb, best_match)
            used_ebooks.add(best_match)
        else:
            unmatched_notebooks.append((nb, best_match, best_ratio))

    return matches, unmatched_notebooks


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
    for notebook_id, (nb, ebook_path) in matches.items():
        conn.execute("""
            INSERT OR REPLACE INTO books (notebook_id, notebook_title, ebook_filename, ebook_path, matched)
            VALUES (?, ?, ?, ?, 1)
        """, (nb["id"], nb["title"], ebook_path.name, str(ebook_path)))
    conn.commit()
    print(f"\n✅ 成功匹配: {len(matches)} 本")

    # 报告未匹配的笔记本
    if unmatched:
        print(f"\n⚠️  未匹配的笔记本 ({len(unmatched)}):")
        for nb, best, ratio in unmatched:
            hint = f" 最接近: {best.stem} ({ratio:.0%})" if best else ""
            print(f"   - {nb['title']}{hint}")
        for nb, best, ratio in unmatched:
            conn.execute("""
                INSERT OR REPLACE INTO books (notebook_id, notebook_title, matched)
                VALUES (?, ?, 0)
            """, (nb["id"], nb["title"]))
        conn.commit()

    # 报告未匹配的电子书
    matched_ebooks = {str(ebook_path) for _, (_, ebook_path) in matches.items()}
    unmatched_ebooks = [e for e in ebooks if str(e) not in matched_ebooks]
    if unmatched_ebooks:
        print(f"\n⚠️  没有对应笔记本的电子书 ({len(unmatched_ebooks)}):")
        for e in unmatched_ebooks:
            print(f"   - {e.name}")

    conn.close()
    print("\n💡 运行 'python3 batch_generate.py status' 查看详情")


# ============================================================
# 生成引擎
# ============================================================

def get_latest_artifact_id(notebook_id, artifact_type="report"):
    """获取指定类型的最新 artifact ID"""
    rc, stdout, stderr = nblm(
        "artifact", "list",
        "--notebook", notebook_id,
        "--type", artifact_type,
        "--json",
        timeout=30,
    )
    if rc != 0:
        return None
    try:
        data = json.loads(stdout)
        artifacts = data.get("artifacts", [])
        if artifacts:
            return artifacts[0]["id"]
    except json.JSONDecodeError:
        pass
    return None


def generate_report(conn, notebook_id, notebook_title, model_key, prompt, download_path):
    """生成单个分析报告，返回 (success, artifact_id)"""
    config = MODEL_CONFIG[model_key]
    label = config["label"]
    status_field = config["status_field"]
    id_field = config["id_field"]
    path_field = config["path_field"]

    print(f"  📝 [{label}] 生成中...", end=" ", flush=True)

    update_status(conn, notebook_id, status_field, "generating")

    # 调用 NotebookLM 生成报告
    rc, stdout, stderr = nblm_with_retry(
        "generate", "report", prompt,
        "--format", "custom",
        "--notebook", notebook_id,
        "--language", "zh_Hans",
        "--wait",
        "--json",
        timeout=900,
    )

    if rc != 0:
        error_msg = f"生成失败 (exit={rc}): {stderr[:200]}"
        update_status(conn, notebook_id, status_field, "failed", error_log=error_msg)
        print(f"❌ {error_msg}")
        return False, None

    # 通过 artifact list 获取刚生成的 artifact ID
    artifact_id = get_latest_artifact_id(notebook_id)

    if not artifact_id:
        update_status(conn, notebook_id, status_field, "failed",
                      error_log="无法从 artifact list 获取 artifact_id")
        print("❌ 无法获取 artifact_id")
        return False, None

    print("✅", end=" ", flush=True)

    # 下载报告
    print("下载...", end=" ", flush=True)
    rc, stdout, stderr = nblm(
        "download", "report", str(download_path),
        "--notebook", notebook_id,
        "--latest",
        timeout=120,
    )

    if rc == 0:
        update_status(conn, notebook_id, status_field, "done",
                      **{id_field: artifact_id, path_field: str(download_path)})
        print("✅")
        return True, artifact_id
    else:
        error_msg = f"下载失败: {stderr[:200]}"
        update_status(conn, notebook_id, status_field, "failed", error_log=error_msg)
        print(f"❌ {error_msg}")
        return False, artifact_id


def cmd_generate(args):
    """批量生成分析报告"""
    check_notebooklm_ready()
    conn = init_db()
    prompts_config = load_prompts()

    # 确定要处理的模型
    if args.only:
        chosen_models = [m.strip() for m in args.only.split(",")]
        # 验证模型名称
        valid_models = set(MODEL_CONFIG.keys())
        for m in chosen_models:
            if m not in valid_models:
                print(f"❌ 未知模型: {m}，可选: {', '.join(valid_models)}")
                sys.exit(1)
    else:
        chosen_models = None  # 表示使用每本书的默认模型列表

    # 查询待处理的书
    if args.retry_failed:
        # 重试：任意模型状态为 failed
        failed_conditions = " OR ".join(
            [f"{MODEL_CONFIG[m]['status_field']} = 'failed'" for m in MODEL_CONFIG]
        )
        where = f"matched = 1 AND ({failed_conditions})"
    else:
        # 新任务：任意模型状态为 pending
        pending_conditions = " OR ".join(
            [f"{MODEL_CONFIG[m]['status_field']} = 'pending'" for m in MODEL_CONFIG]
        )
        where = f"matched = 1 AND ({pending_conditions})"

    book_where, book_params = where_book(args.book)
    sql = f"SELECT * FROM books WHERE {where}{book_where} ORDER BY id"
    if args.limit > 0:
        sql += f" LIMIT {args.limit}"

    rows = conn.execute(sql, book_params).fetchall()

    if not rows:
        print("🎉 没有待处理的任务！")
        conn.close()
        return

    print(f"📋 待处理: {len(rows)} 本书\n")

    total_book = 0

    for row in rows:
        if interrupted:
            print("⏸️  已中断，进度已保存")
            break

        notebook_id = row[1]
        notebook_title = row[2]
        ebook_path = row[4]

        base_name = Path(ebook_path).stem if ebook_path else notebook_title
        output_dir = PDF_DIR / base_name
        output_dir.mkdir(parents=True, exist_ok=True)

        # 获取该书的模型列表
        models = chosen_models if chosen_models else get_models_for_book(notebook_title, prompts_config)

        total_book += 1
        print(f"\n📖 [{total_book}/{len(rows)}] {notebook_title}")

        for model_key in models:
            if interrupted:
                break

            config = MODEL_CONFIG[model_key]
            status_field = config["status_field"]

            # 检查当前状态
            status_row = conn.execute(
                f"SELECT {status_field} FROM books WHERE notebook_id = ?",
                (notebook_id,)
            ).fetchone()

            if status_row and status_row[0] == "done":
                print(f"  ⏭️  [{config['label']}]: 已完成，跳过")
                continue

            if status_row and status_row[0] == "failed" and not args.retry_failed:
                print(f"  ⏭️  [{config['label']}]: 之前失败，使用 --retry-failed 重试")
                continue

            # 获取该模型的 prompt
            model_prompt = prompts_config["models"].get(model_key, "")
            if not model_prompt:
                print(f"  ❌ [{config['label']}]: 未找到 prompt")
                continue

            download_path = output_dir / f"{base_name}{config['suffix']}"

            ok, _ = generate_report(
                conn, notebook_id, notebook_title,
                model_key, model_prompt.strip(), download_path
            )

            # 模型间休息
            time.sleep(2)

        # 书之间额外休息
        time.sleep(5)

    conn.close()
    print(f"\n{'='*50}")
    print("🏁 完成！运行 'python3 batch_generate.py status' 查看结果")


# ============================================================
# 状态查看
# ============================================================

def cmd_status():
    """查看当前进度"""
    conn = init_db()
    columns = ["notebook_title"] + [f"{MODEL_CONFIG[m]['status_field']}" for m in MODEL_CONFIG]
    sql = f"SELECT {', '.join(columns)} FROM books ORDER BY id"
    rows = conn.execute(sql).fetchall()

    if not rows:
        print("📭 还没有任何记录。请先运行 discover 子命令。")
        conn.close()
        return

    headers = ["笔记本"] + [MODEL_CONFIG[m]["label"] for m in MODEL_CONFIG]
    header_line = f"{headers[0]:<30} {headers[1]:<12} {headers[2]:<12} {headers[3]:<12} {headers[4]:<14} {headers[5]:<14}"
    print(header_line)
    print("-" * 100)

    for r in rows:
        title = r[0][:28] if len(r[0]) > 28 else r[0]
        status_icons = []
        for s in r[1:6]:
            if s == "done":
                status_icons.append("✅")
            elif s == "failed":
                status_icons.append("❌")
            elif s == "generating":
                status_icons.append("🔄")
            else:
                status_icons.append("⏳")
        print(f"{title:<30} {status_icons[0]:<12} {status_icons[1]:<12} {status_icons[2]:<12} {status_icons[3]:<14} {status_icons[4]:<14}")

    conn.close()


# ============================================================
# 信息图生成
# ============================================================

def cmd_infographic(args):
    """为已完成的分析报告批量生成信息图"""
    check_notebooklm_ready()
    conn = init_db()

    if args.style not in INFOGRAPHIC_STYLES:
        print(f"❌ 未知风格: {args.style}，可选: {', '.join(INFOGRAPHIC_STYLES)}")
        sys.exit(1)

    # 确定要处理的模型
    if args.only:
        chosen_models = [m.strip() for m in args.only.split(",")]
        for m in chosen_models:
            if m not in MODEL_CONFIG:
                print(f"❌ 未知模型: {m}，可选: {', '.join(MODEL_CONFIG.keys())}")
                sys.exit(1)
    else:
        chosen_models = list(MODEL_CONFIG.keys())

    # 查询有已完成报告的书（任意模型 status = done）
    done_conditions = " OR ".join(
        [f"{MODEL_CONFIG[m]['status_field']} = 'done'" for m in chosen_models]
    )
    book_where, book_params = where_book(args.book)
    sql = f"SELECT * FROM books WHERE matched = 1 AND ({done_conditions}){book_where} ORDER BY id"
    if args.limit > 0:
        sql += f" LIMIT {args.limit}"

    rows = conn.execute(sql, book_params).fetchall()

    if not rows:
        print("📭 没有已完成报告的书。请先运行 generate 生成分析报告。")
        conn.close()
        return

    print(f"📋 待处理: {len(rows)} 本书，风格: {args.style}\n")

    for row in rows:
        if interrupted:
            print("⏸️  已中断")
            break

        notebook_id = row[1]
        notebook_title = row[2]
        ebook_path = row[4]

        base_name = Path(ebook_path).stem if ebook_path else notebook_title
        output_dir = PDF_DIR / base_name
        output_dir.mkdir(parents=True, exist_ok=True)

        print(f"📖 {notebook_title}")

        for model_key in chosen_models:
            if interrupted:
                break

            config = MODEL_CONFIG[model_key]
            status_field = config["status_field"]
            label = config["label"]

            # 只处理报告已完成的模型
            status_row = conn.execute(
                f"SELECT {status_field} FROM books WHERE notebook_id = ?",
                (notebook_id,)
            ).fetchone()

            if not status_row or status_row[0] != "done":
                print(f"  ⏭️  [{label}]: 报告未完成，跳过")
                continue

            # 生成信息图
            prompt = INFOGRAPHIC_PROMPTS.get(model_key, "")
            if not prompt:
                print(f"  ❌ [{label}]: 无信息图 prompt")
                continue

            output_path = output_dir / f"{base_name}{config['suffix'].replace('.md', '_信息图.png')}"
            if output_path.exists() and not args.force:
                print(f"  ⏭️  [{label} 信息图]: 已存在，跳过")
                continue

            print(f"  📊 [{label} 信息图] 生成中...", end=" ", flush=True)

            rc, stdout, stderr = nblm_with_retry(
                "generate", "infographic", prompt,
                "--notebook", notebook_id,
                "--language", "zh_Hans",
                "--style", args.style,
                "--detail", "detailed",
                "--wait",
                "--json",
                timeout=900,
            )

            if rc != 0:
                print(f"❌ 生成失败: {stderr[:100]}")
                continue

            # 获取最新 artifact
            artifact_id = get_latest_artifact_id(notebook_id, "infographic")
            if not artifact_id:
                print("❌ 无法获取 artifact_id")
                continue

            print("✅", end=" ", flush=True)

            # 下载
            rc, stdout, stderr = nblm(
                "download", "infographic", str(output_path),
                "--notebook", notebook_id,
                "--artifact", artifact_id,
                timeout=120,
            )

            if rc == 0:
                print("✅")
            else:
                print(f"❌ 下载失败: {stderr[:100]}")

            time.sleep(3)

        time.sleep(5)

    conn.close()
    print(f"\n{'='*50}")
    print("🏁 完成！")


# ============================================================
# 思维导图生成
# ============================================================

def cmd_mindmap(args):
    """为匹配的电子书生成思维导图（基于全书目录和大纲）"""
    check_notebooklm_ready()
    conn = init_db()

    book_where, book_params = where_book(args.book)
    sql = f"SELECT * FROM books WHERE matched = 1{book_where} ORDER BY id"
    if args.limit > 0:
        sql += f" LIMIT {args.limit}"

    rows = conn.execute(sql, book_params).fetchall()

    if not rows:
        print("📭 没有已匹配的书。请先运行 discover。")
        conn.close()
        return

    print(f"📋 待处理: {len(rows)} 本书\n")

    for row in rows:
        if interrupted:
            print("⏸️  已中断")
            break

        notebook_id = row[1]
        notebook_title = row[2]
        ebook_path = row[4]

        base_name = Path(ebook_path).stem if ebook_path else notebook_title
        output_dir = PDF_DIR / base_name
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{base_name}_思维导图.json"

        if output_path.exists() and not args.force:
            print(f"⏭️  [{notebook_title}] 思维导图已存在，跳过")
            continue

        print(f"🧠 [{notebook_title}] 生成中...", end=" ", flush=True)

        rc, stdout, stderr = nblm(
            "generate", "mind-map",
            "--notebook", notebook_id,
            "--json",
            timeout=120,
        )

        if rc != 0:
            print(f"❌ 生成失败: {stderr[:100]}")
            continue

        # 获取 artifact
        artifact_id = get_latest_artifact_id(notebook_id, "mind-map")
        if not artifact_id:
            print("❌ 无法获取 artifact_id")
            continue

        # 下载
        rc, stdout, stderr = nblm(
            "download", "mind-map", str(output_path),
            "--notebook", notebook_id,
            "--artifact", artifact_id,
            timeout=120,
        )

        if rc == 0:
            print(f"✅ ({output_path.name})")
        else:
            print(f"❌ 下载失败: {stderr[:100]}")

        time.sleep(2)

    conn.close()
    print(f"\n{'='*50}")
    print("🏁 完成！")


# ============================================================
# 演示文稿生成
# ============================================================

def poll_artifact_complete(notebook_id, artifact_type, before_count, max_wait=1200):
    """轮询等待新 artifact 完成，返回 artifact 或 None（超时）"""
    for _ in range(max_wait // 5):
        time.sleep(5)
        try:
            rc, stdout, _ = nblm(
                "artifact", "list",
                "--notebook", notebook_id,
                "--type", artifact_type,
                "--json",
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            continue
        if rc != 0:
            continue
        try:
            arts = json.loads(stdout).get("artifacts", [])
        except json.JSONDecodeError:
            continue
        if len(arts) > before_count:
            newest = arts[0]
            if newest.get("status") == "completed":
                return newest
    return None


def cmd_slides(args):
    """为匹配的电子书生成演示文稿（slide-deck）"""
    check_notebooklm_ready()
    conn = init_db()

    if args.only:
        chosen_models = [m.strip() for m in args.only.split(",")]
        for m in chosen_models:
            if m not in MODEL_CONFIG:
                print(f"❌ 未知模型: {m}，可选: {', '.join(MODEL_CONFIG.keys())}")
                sys.exit(1)
    else:
        chosen_models = list(MODEL_CONFIG.keys())

    book_where, book_params = where_book(args.book)
    sql = f"SELECT * FROM books WHERE matched = 1{book_where} ORDER BY id"
    if args.limit > 0:
        sql += f" LIMIT {args.limit}"

    rows = conn.execute(sql, book_params).fetchall()

    if not rows:
        print("📭 没有已匹配的书。请先运行 discover。")
        conn.close()
        return

    print(f"📋 待处理: {len(rows)} 本书\n")

    for row in rows:
        if interrupted:
            print("⏸️  已中断")
            break

        notebook_id = row[1]
        notebook_title = row[2]
        ebook_path = row[4]

        base_name = Path(ebook_path).stem if ebook_path else notebook_title
        output_dir = PDF_DIR / base_name
        output_dir.mkdir(parents=True, exist_ok=True)

        print(f"📖 {notebook_title}")

        for model_key in chosen_models:
            if interrupted:
                break

            config = MODEL_CONFIG[model_key]
            label = config["label"]
            prompt = SLIDE_PROMPTS.get(model_key, "")

            output_path = output_dir / f"{base_name}{config['suffix'].replace('.md', '_演示文稿.pptx')}"
            if output_path.exists() and not args.force:
                print(f"  ⏭️  [{label} 演示文稿]: 已存在，跳过")
                continue

            # 记录生成前的 artifact 数量
            rc, stdout, _ = nblm(
                "artifact", "list",
                "--notebook", notebook_id,
                "--type", "slide-deck",
                "--json",
                timeout=30,
            )
            before_count = len(json.loads(stdout).get("artifacts", [])) if rc == 0 else 0

            print(f"  📊 [{label} 演示文稿] 触发生成...", end=" ", flush=True)

            rc, stdout, stderr = nblm(
                "generate", "slide-deck", prompt,
                "--notebook", notebook_id,
                "--language", "zh_Hans",
                "--format", "detailed",
                "--no-wait",
                "--json",
                timeout=30,
            )

            if rc != 0:
                print(f"❌ 启动失败: {stderr[:100]}")
                continue

            # 轮询等待完成（slide-deck 生成较慢，最长等 20 分钟）
            print("轮询中...", end=" ", flush=True)
            artifact = poll_artifact_complete(notebook_id, "slide-deck", before_count, max_wait=1200)

            if not artifact:
                print("❌ 超时（>20分钟）")
                continue

            print("✅", end=" ", flush=True)

            # 下载
            rc, stdout, stderr = nblm(
                "download", "slide-deck", str(output_path),
                "--notebook", notebook_id,
                "--artifact", artifact["id"],
                "--format", "pptx",
                timeout=120,
            )

            if rc == 0:
                size_kb = output_path.stat().st_size // 1024
                print(f"✅ ({size_kb}KB)")
            else:
                print(f"❌ 下载失败: {stderr[:100]}")

        time.sleep(3)

    conn.close()
    print(f"\n{'='*50}")
    print("🏁 完成！")


# ============================================================
# 闪卡生成
# ============================================================

def cmd_flashcards(args):
    """为匹配的电子书生成本书关键概念闪卡"""
    check_notebooklm_ready()
    conn = init_db()

    book_where, book_params = where_book(args.book)
    sql = f"SELECT * FROM books WHERE matched = 1{book_where} ORDER BY id"
    if args.limit > 0:
        sql += f" LIMIT {args.limit}"

    rows = conn.execute(sql, book_params).fetchall()

    if not rows:
        print("📭 没有已匹配的书。请先运行 discover。")
        conn.close()
        return

    print(f"📋 待处理: {len(rows)} 本书\n")

    for row in rows:
        if interrupted:
            print("⏸️  已中断")
            break

        notebook_id = row[1]
        notebook_title = row[2]
        ebook_path = row[4]

        base_name = Path(ebook_path).stem if ebook_path else notebook_title
        output_dir = PDF_DIR / base_name
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{base_name}_闪卡.md"

        if output_path.exists() and not args.force:
            print(f"⏭️  [{notebook_title}] 闪卡已存在，跳过")
            continue

        # 记录生成前的数量
        rc, stdout, _ = nblm("artifact", "list", "--notebook", notebook_id, "--type", "flashcard", "--json", timeout=30)
        before_count = len(json.loads(stdout).get("artifacts", [])) if rc == 0 else 0

        print(f"🃏 [{notebook_title}] 生成闪卡...", end=" ", flush=True)

        rc, stdout, stderr = nblm(
            "generate", "flashcards", FLASHCARD_PROMPT,
            "--notebook", notebook_id,
            "--no-wait",
            "--json",
            timeout=30,
        )

        if rc != 0:
            print(f"❌ 启动失败: {stderr[:100]}")
            continue

        print("轮询中...", end=" ", flush=True)
        artifact = poll_artifact_complete(notebook_id, "flashcards", before_count, max_wait=600)

        if not artifact:
            print("❌ 超时")
            continue

        print("✅", end=" ", flush=True)

        rc, stdout, stderr = nblm(
            "download", "flashcards", str(output_path),
            "--notebook", notebook_id,
            "--artifact", artifact["id"],
            "--format", "markdown",
            timeout=120,
        )

        if rc == 0:
            print(f"✅ ({output_path.name})")
        else:
            print(f"❌ 下载失败: {stderr[:100]}")

    conn.close()
    print(f"\n{'='*50}")
    print("🏁 完成！")


# ============================================================
# 测验生成
# ============================================================

def cmd_quiz(args):
    """为匹配的电子书生成 3 个难度级别的测验题"""
    check_notebooklm_ready()
    conn = init_db()

    difficulties = ["easy", "medium", "hard"] if args.difficulty is None else [args.difficulty]

    book_where, book_params = where_book(args.book)
    sql = f"SELECT * FROM books WHERE matched = 1{book_where} ORDER BY id"
    if args.limit > 0:
        sql += f" LIMIT {args.limit}"

    rows = conn.execute(sql, book_params).fetchall()

    if not rows:
        print("📭 没有已匹配的书。请先运行 discover。")
        conn.close()
        return

    print(f"📋 待处理: {len(rows)} 本书 × {len(difficulties)} 难度\n")

    for row in rows:
        if interrupted:
            print("⏸️  已中断")
            break

        notebook_id = row[1]
        notebook_title = row[2]
        ebook_path = row[4]

        base_name = Path(ebook_path).stem if ebook_path else notebook_title
        output_dir = PDF_DIR / base_name
        output_dir.mkdir(parents=True, exist_ok=True)

        print(f"📖 {notebook_title}")

        for diff in difficulties:
            if interrupted:
                break

            diff_label = {"easy": "简单", "medium": "中等", "hard": "困难"}[diff]
            output_path = output_dir / f"{base_name}_测验_{diff_label}.md"

            if output_path.exists() and not args.force:
                print(f"  ⏭️  测验({diff_label}): 已存在，跳过")
                continue

            prompt = QUIZ_PROMPTS[diff]

            # 记录生成前的数量
            rc, stdout, _ = nblm("artifact", "list", "--notebook", notebook_id, "--type", "quiz", "--json", timeout=30)
            before_count = len(json.loads(stdout).get("artifacts", [])) if rc == 0 else 0

            print(f"  📝 测验({diff_label}) 生成中...", end=" ", flush=True)

            rc, stdout, stderr = nblm(
                "generate", "quiz", prompt,
                "--notebook", notebook_id,
                "--difficulty", diff,
                "--no-wait",
                "--json",
                timeout=30,
            )

            if rc != 0:
                print(f"❌ 启动失败: {stderr[:100]}")
                continue

            print("轮询中...", end=" ", flush=True)
            artifact = poll_artifact_complete(notebook_id, "quiz", before_count, max_wait=600)

            if not artifact:
                print("❌ 超时")
                continue

            print("✅", end=" ", flush=True)

            rc, stdout, stderr = nblm(
                "download", "quiz", str(output_path),
                "--notebook", notebook_id,
                "--artifact", artifact["id"],
                "--format", "markdown",
                timeout=120,
            )

            if rc == 0:
                print(f"✅ ({output_path.name})")
            else:
                print(f"❌ 下载失败: {stderr[:100]}")

            time.sleep(2)

    conn.close()
    print(f"\n{'='*50}")
    print("🏁 完成！")


# ============================================================
# 入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="电子书批量笔记生成系统 — 5 种分析模型 × 每本电子书",
        epilog="""
示例:
  python3 -u batch_generate.py discover                    # 发现并匹配
  python3 -u batch_generate.py status                       # 查看进度
  python3 -u batch_generate.py generate                     # 全量生成
  python3 -u batch_generate.py generate --limit 3           # 只生成 3 本
  python3 -u batch_generate.py generate --only axiom,bayes  # 只生成指定模型
  python3 -u batch_generate.py generate --retry-failed      # 重试失败项
  python3 -u batch_generate.py infographic                  # 为已完成报告生成信息图
  python3 -u batch_generate.py infographic --style bricks   # 指定信息图风格
  python3 -u batch_generate.py infographic --only axiom     # 只生成指定模型信息图
  python3 -u batch_generate.py mindmap                       # 生成思维导图
  python3 -u batch_generate.py mindmap --limit 1             # 只生成 1 本思维导图
  python3 -u batch_generate.py slides                        # 生成演示文稿（较慢，轮询等待）
  python3 -u batch_generate.py slides --only axiom           # 只生成指定模型演示文稿
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("discover", help="发现并匹配笔记本和电子书")

    gen_parser = subparsers.add_parser("generate", help="批量生成分析报告")
    gen_parser.add_argument("--book", type=str, help="按书名过滤")
    gen_parser.add_argument("--limit", type=int, default=0, help="最多处理 N 本")
    gen_parser.add_argument("--retry-failed", action="store_true", help="重试失败项")
    gen_parser.add_argument("--only", type=str, help="只生成指定模型 (逗号分隔)")

    info_parser = subparsers.add_parser("infographic", help="为已完成报告生成信息图")
    info_parser.add_argument("--book", type=str, help="按书名过滤")
    info_parser.add_argument("--limit", type=int, default=0, help="最多处理 N 本")
    info_parser.add_argument("--only", type=str, help="只生成指定模型 (逗号分隔)")
    info_parser.add_argument("--style", type=str, default="sketch-note",
                             help=f"信息图风格，可选: {', '.join(INFOGRAPHIC_STYLES)} (默认: sketch-note)")
    info_parser.add_argument("--force", action="store_true", help="覆盖已有文件")

    mind_parser = subparsers.add_parser("mindmap", help="生成思维导图（基于全书目录大纲）")
    mind_parser.add_argument("--book", type=str, help="按书名过滤")
    mind_parser.add_argument("--limit", type=int, default=0, help="最多处理 N 本")
    mind_parser.add_argument("--force", action="store_true", help="覆盖已有文件")

    slides_parser = subparsers.add_parser("slides", help="生成演示文稿（slide-deck，轮询等待完成）")
    slides_parser.add_argument("--book", type=str, help="按书名过滤")
    slides_parser.add_argument("--limit", type=int, default=0, help="最多处理 N 本")
    slides_parser.add_argument("--only", type=str, help="只生成指定模型 (逗号分隔)")
    slides_parser.add_argument("--force", action="store_true", help="覆盖已有文件")

    fc_parser = subparsers.add_parser("flashcards", help="生成关键概念闪卡")
    fc_parser.add_argument("--book", type=str, help="按书名过滤")
    fc_parser.add_argument("--limit", type=int, default=0, help="最多处理 N 本")
    fc_parser.add_argument("--force", action="store_true", help="覆盖已有文件")

    quiz_parser = subparsers.add_parser("quiz", help="生成测验题（3 个难度）")
    quiz_parser.add_argument("--book", type=str, help="按书名过滤")
    quiz_parser.add_argument("--limit", type=int, default=0, help="最多处理 N 本")
    quiz_parser.add_argument("--difficulty", type=str, choices=["easy", "medium", "hard"],
                             help="只生成指定难度（默认全部 3 个）")
    quiz_parser.add_argument("--force", action="store_true", help="覆盖已有文件")

    subparsers.add_parser("status", help="查看进度")

    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        return

    # 确保目录存在
    PDF_DIR.mkdir(parents=True, exist_ok=True)

    if args.command == "discover":
        cmd_discover()
    elif args.command == "generate":
        cmd_generate(args)
    elif args.command == "infographic":
        cmd_infographic(args)
    elif args.command == "mindmap":
        cmd_mindmap(args)
    elif args.command == "slides":
        cmd_slides(args)
    elif args.command == "flashcards":
        cmd_flashcards(args)
    elif args.command == "quiz":
        cmd_quiz(args)
    elif args.command == "status":
        cmd_status()


if __name__ == "__main__":
    main()
