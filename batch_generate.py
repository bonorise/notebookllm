#!/usr/bin/env python3
"""电子书批量笔记生成系统 — 按书名自适应选择阅读模型后再生成 NotebookLM 产物。

核心变化：
1. 不再默认一次性跑所有模型。
2. 先根据书名 + 可选搜索资料 + DeepSeek/启发式规则选择 3-4 个合适模型。
3. 报告、信息图、演示文稿默认只按已选择模型生成。
4. 保留 discover / generate / infographic / slides / mindmap / flashcards / quiz / status 工作流。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    raise SystemExit("请先安装 PyYAML: pip install pyyaml") from exc

from book_model_selector import select_models_for_book

# 路径常量，可用环境变量覆盖，方便本地迁移
BASE_DIR = Path(__file__).parent.resolve()
PDF_DIR = Path(os.getenv("PDF_DIR", "/Users/liubo/Desktop/Learning/reading/PDF"))
PROMPTS_DIR = BASE_DIR / "prompts"
READING_MODELS_DIR = BASE_DIR / "reading_models"
DB_PATH = BASE_DIR / "books.db"
MODEL_SELECTION_FILE = PROMPTS_DIR / "model_selection.yaml"

interrupted = False


def signal_handler(sig, frame):
    global interrupted
    print("\n⚠️  收到中断信号，正在安全退出...")
    print("   当前进度已保存，下次运行会自动跳过已完成产物。")
    interrupted = True


signal.signal(signal.SIGINT, signal_handler)


# ============================================================
# 基础工具
# ============================================================


def ensure_output_dir(base_name: str) -> Path:
    """创建书籍输出子目录，若 PDF 在 PDF_DIR 根目录则移入。"""
    output_dir = PDF_DIR / base_name
    output_dir.mkdir(parents=True, exist_ok=True)
    pdf_in_root = PDF_DIR / f"{base_name}.pdf"
    if pdf_in_root.exists():
        target = output_dir / f"{base_name}.pdf"
        if not target.exists():
            shutil.move(str(pdf_in_root), str(target))
    return output_dir


def nblm(*args: str, timeout: int = 600) -> tuple[int, str, str]:
    """调用 notebooklm CLI，返回 (returncode, stdout, stderr)。"""
    cmd = [os.getenv("NOTEBOOKLM_BIN", "notebooklm"), *args]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def nblm_with_retry(*args: str, max_retries: int = 3, timeout: int = 900) -> tuple[int, str, str]:
    """带重试的 notebooklm 调用，遇到速率限制自动退避。"""
    last = (1, "", "")
    for attempt in range(max_retries):
        try:
            rc, stdout, stderr = nblm(*args, timeout=timeout)
            last = (rc, stdout, stderr)
            if rc == 0:
                return rc, stdout, stderr
            text = f"{stdout}\n{stderr}".lower()
            if "rate" in text or "limit" in text or "rate_limited" in text:
                wait = (attempt + 1) * 300
                print(f"    ⏳ 速率限制，等待 {wait}s 后重试...")
                time.sleep(wait)
                continue
            return rc, stdout, stderr
        except subprocess.TimeoutExpired:
            if attempt < max_retries - 1:
                print(f"    ⏱️  超时，重试 ({attempt + 2}/{max_retries})...")
                time.sleep(10)
            else:
                raise
    return last


def check_notebooklm_ready() -> None:
    """检查 NotebookLM CLI 是否已登录且可用。"""
    rc, stdout, stderr = nblm("status", timeout=15)
    if rc != 0:
        print("❌ NotebookLM CLI 不可用。请检查:")
        print("   1. pip install notebooklm-py")
        print("   2. notebooklm login")
        print(f"   stderr: {stderr[:200]}")
        sys.exit(1)
    print("✅ NotebookLM 已就绪")


def safe_json_loads(text: str, default: Any = None) -> Any:
    try:
        return json.loads(text)
    except Exception:
        return default


# ============================================================
# 配置与模型
# ============================================================


def load_model_registry() -> Dict[str, Any]:
    if not MODEL_SELECTION_FILE.exists():
        print(f"❌ 未找到模型配置: {MODEL_SELECTION_FILE}")
        sys.exit(1)
    with open(MODEL_SELECTION_FILE, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if "models" not in data:
        print("❌ prompts/model_selection.yaml 缺少 models 配置")
        sys.exit(1)
    return data


def get_model_keys(registry: Dict[str, Any]) -> List[str]:
    return list(registry["models"].keys())


def validate_models(models: Iterable[str], registry: Dict[str, Any]) -> List[str]:
    valid = set(get_model_keys(registry))
    out = []
    for raw in models:
        m = raw.strip()
        if not m:
            continue
        if m not in valid:
            print(f"❌ 未知模型: {m}")
            print(f"   可选: {', '.join(get_model_keys(registry))}")
            sys.exit(1)
        out.append(m)
    return out


def load_prompt_for_model(model_key: str, registry: Dict[str, Any]) -> str:
    meta = registry["models"][model_key]
    prompt_path = READING_MODELS_DIR / meta["file"]
    if not prompt_path.exists():
        print(f"❌ 模型 prompt 文件不存在: {prompt_path}")
        sys.exit(1)
    return prompt_path.read_text(encoding="utf-8")


def model_label(model_key: str, registry: Dict[str, Any]) -> str:
    return registry["models"][model_key].get("label", model_key)


def model_suffix(model_key: str, registry: Dict[str, Any]) -> str:
    return registry["models"][model_key].get("suffix", f"_{model_key}.md")


def model_report_path(base_name: str, output_dir: Path, model_key: str, registry: Dict[str, Any]) -> Path:
    return output_dir / f"{base_name}{model_suffix(model_key, registry)}"


def model_infographic_path(base_name: str, output_dir: Path, model_key: str, registry: Dict[str, Any]) -> Path:
    return output_dir / f"{base_name}{model_suffix(model_key, registry).replace('.md', '_信息图.png')}"


def model_slides_path(base_name: str, output_dir: Path, model_key: str, registry: Dict[str, Any]) -> Path:
    return output_dir / f"{base_name}{model_suffix(model_key, registry).replace('.md', '_演示文稿.pptx')}"


INFOGRAPHIC_STYLES = [
    "auto", "sketch-note", "professional", "bento-grid", "editorial",
    "instructional", "bricks", "clay", "anime", "kawaii", "scientific",
]

FLASHCARD_PROMPT = "请为本书生成关键概念闪卡，正面是概念/术语/问题，背面是解释/定义/答案。涵盖全书最重要的概念、理论和框架。用中文。"

QUIZ_PROMPTS = {
    "easy": "请为本书生成一份简单难度的测验题，测试读者对书中基本概念和核心观点的理解。题型以选择和判断为主。用中文。",
    "medium": "请为本书生成一份中等难度的测验题，测试读者对书中理论框架、关键论证和概念之间关系的理解。包含选择、填空和简答。用中文。",
    "hard": "请为本书生成一份困难难度的测验题，测试读者对书中深层逻辑、批判性分析和跨章节综合的掌握。包含分析题和论述题。用中文。",
}




def table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    """Return column names for a table; empty set when table does not exist."""
    try:
        return {row[1] for row in conn.execute(f'PRAGMA table_info("{table_name}")').fetchall()}
    except sqlite3.Error:
        return set()


def migrate_legacy_books_status(conn: sqlite3.Connection) -> None:
    """Migrate old wide-table model status columns into model_runs.

    老版本 books.db 把 5 个模型的状态直接存在 books 表中，例如：
    axiom_status / axiom_artifact_id / axiom_path。

    新版本支持 11 个模型，使用 model_runs(notebook_id, model_key, status, artifact_id, output_path)
    保存每个模型的一条运行记录。这个迁移是幂等的：重复运行不会破坏已有新记录。
    """
    cols = table_columns(conn, "books")
    if not {"notebook_id", "notebook_title"}.issubset(cols):
        return

    legacy_map = {
        "axiom": ("axiom_status", "axiom_artifact_id", "axiom_path"),
        "bayes": ("bayes_status", "bayes_artifact_id", "bayes_path"),
        "first_principles": ("first_principles_status", "first_principles_artifact_id", "first_principles_path"),
        "dialectic": ("dialectic_status", "dialectic_artifact_id", "dialectic_path"),
    }

    available = {
        model_key: fields
        for model_key, fields in legacy_map.items()
        if fields[0] in cols
    }
    if not available:
        return

    rows = conn.execute("SELECT * FROM books").fetchall()
    migrated = 0
    for row in rows:
        notebook_id = row["notebook_id"]
        if not notebook_id:
            continue
        for model_key, (status_col, artifact_col, path_col) in available.items():
            status = row[status_col] if status_col in row.keys() else None
            artifact_id = row[artifact_col] if artifact_col in row.keys() else None
            output_path = row[path_col] if path_col in row.keys() else None
            if not status and not output_path and not artifact_id:
                continue
            status = status or "pending"
            # 不覆盖已经是 done/generating 的新记录；只补充缺失或 pending 的记录。
            conn.execute("""
                INSERT INTO model_runs (notebook_id, model_key, status, artifact_id, output_path, updated_at)
                VALUES (?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(notebook_id, model_key) DO UPDATE SET
                    status=CASE
                        WHEN model_runs.status IS NULL OR model_runs.status IN ('pending', '') THEN excluded.status
                        ELSE model_runs.status
                    END,
                    artifact_id=COALESCE(model_runs.artifact_id, excluded.artifact_id),
                    output_path=COALESCE(model_runs.output_path, excluded.output_path),
                    updated_at=datetime('now')
            """, (notebook_id, model_key, status, artifact_id, output_path))
            migrated += 1

    # academic 是旧版“学术拆解/新旧范式”模型，和新版 model03/model06 的结构地图不完全等价，
    # 因此不自动映射到 structure 或 structure_quick，避免把旧报告误认为新版结构地图报告。
    if "academic_status" in cols:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS legacy_academic_runs (
                notebook_id TEXT PRIMARY KEY,
                status TEXT,
                artifact_id TEXT,
                output_path TEXT,
                note TEXT DEFAULT '旧版 academic/学术拆解模型记录，未自动映射到新版 structure 模型',
                updated_at TEXT DEFAULT (datetime('now'))
            )
        """)
        for row in rows:
            notebook_id = row["notebook_id"]
            status = row["academic_status"] if "academic_status" in row.keys() else None
            artifact_id = row["academic_artifact_id"] if "academic_artifact_id" in row.keys() else None
            output_path = row["academic_path"] if "academic_path" in row.keys() else None
            if status or artifact_id or output_path:
                conn.execute("""
                    INSERT INTO legacy_academic_runs (notebook_id, status, artifact_id, output_path, updated_at)
                    VALUES (?, ?, ?, ?, datetime('now'))
                    ON CONFLICT(notebook_id) DO UPDATE SET
                        status=excluded.status,
                        artifact_id=COALESCE(excluded.artifact_id, legacy_academic_runs.artifact_id),
                        output_path=COALESCE(excluded.output_path, legacy_academic_runs.output_path),
                        updated_at=datetime('now')
                """, (notebook_id, status, artifact_id, output_path))

    conn.commit()


# ============================================================
# 数据库
# ============================================================


def init_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS books (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            notebook_id TEXT NOT NULL UNIQUE,
            notebook_title TEXT NOT NULL,
            ebook_filename TEXT,
            ebook_path TEXT,
            matched INTEGER DEFAULT 0,
            error_log TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS model_selections (
            notebook_id TEXT PRIMARY KEY,
            notebook_title TEXT NOT NULL,
            selected_models TEXT NOT NULL,
            book_profile TEXT DEFAULT '{}',
            search_data TEXT DEFAULT '{}',
            rationale TEXT DEFAULT '',
            source TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS model_runs (
            notebook_id TEXT NOT NULL,
            model_key TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            artifact_id TEXT,
            output_path TEXT,
            error_log TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (notebook_id, model_key)
        )
    """)
    conn.commit()
    migrate_legacy_books_status(conn)
    return conn


def where_book(book_name: Optional[str]) -> tuple[str, List[str]]:
    if book_name:
        return " AND notebook_title LIKE ?", [f"%{book_name}%"]
    return "", []


def update_run(conn: sqlite3.Connection, notebook_id: str, model_key: str, status: str,
               artifact_id: Optional[str] = None, output_path: Optional[str] = None,
               error_log: Optional[str] = None) -> None:
    conn.execute("""
        INSERT INTO model_runs (notebook_id, model_key, status, artifact_id, output_path, error_log, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(notebook_id, model_key) DO UPDATE SET
            status=excluded.status,
            artifact_id=COALESCE(excluded.artifact_id, model_runs.artifact_id),
            output_path=COALESCE(excluded.output_path, model_runs.output_path),
            error_log=COALESCE(excluded.error_log, model_runs.error_log),
            updated_at=datetime('now')
    """, (notebook_id, model_key, status, artifact_id, output_path, error_log))
    conn.commit()


def get_run_status(conn: sqlite3.Connection, notebook_id: str, model_key: str) -> str:
    row = conn.execute(
        "SELECT status FROM model_runs WHERE notebook_id=? AND model_key=?",
        (notebook_id, model_key),
    ).fetchone()
    return row["status"] if row else "pending"


def save_selection(conn: sqlite3.Connection, notebook_id: str, notebook_title: str, selection: Dict[str, Any]) -> None:
    selected_models = selection.get("selected_models", [])
    conn.execute("""
        INSERT INTO model_selections
            (notebook_id, notebook_title, selected_models, book_profile, search_data, rationale, source, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(notebook_id) DO UPDATE SET
            notebook_title=excluded.notebook_title,
            selected_models=excluded.selected_models,
            book_profile=excluded.book_profile,
            search_data=excluded.search_data,
            rationale=excluded.rationale,
            source=excluded.source,
            updated_at=datetime('now')
    """, (
        notebook_id,
        notebook_title,
        json.dumps(selected_models, ensure_ascii=False),
        json.dumps(selection.get("book_profile", {}), ensure_ascii=False),
        json.dumps(selection.get("search", {}), ensure_ascii=False),
        selection.get("rationale", ""),
        selection.get("source", ""),
    ))
    for model_key in selected_models:
        conn.execute("""
            INSERT OR IGNORE INTO model_runs (notebook_id, model_key, status)
            VALUES (?, ?, 'pending')
        """, (notebook_id, model_key))
    conn.commit()


def load_saved_selection(conn: sqlite3.Connection, notebook_id: str) -> Optional[Dict[str, Any]]:
    row = conn.execute("SELECT * FROM model_selections WHERE notebook_id=?", (notebook_id,)).fetchone()
    if not row:
        return None
    return {
        "notebook_id": row["notebook_id"],
        "book_title": row["notebook_title"],
        "selected_models": safe_json_loads(row["selected_models"], []),
        "book_profile": safe_json_loads(row["book_profile"], {}),
        "search": safe_json_loads(row["search_data"], {}),
        "rationale": row["rationale"],
        "source": row["source"],
    }


def get_or_create_selection(conn: sqlite3.Connection, row: sqlite3.Row, args: argparse.Namespace,
                            registry: Dict[str, Any]) -> Dict[str, Any]:
    saved = load_saved_selection(conn, row["notebook_id"])
    if saved and not getattr(args, "force_select", False):
        return saved
    selection = select_models_for_book(
        row["notebook_title"],
        search_enabled=not getattr(args, "no_search", False),
        force_search=getattr(args, "force_select", False),
        min_models=getattr(args, "min_models", None),
        max_models=getattr(args, "max_models", None),
        use_llm=not getattr(args, "no_llm", False),
    )
    save_selection(conn, row["notebook_id"], row["notebook_title"], selection)
    return selection


def models_for_work(conn: sqlite3.Connection, row: sqlite3.Row, args: argparse.Namespace,
                    registry: Dict[str, Any]) -> List[str]:
    if getattr(args, "only", None):
        return validate_models(args.only.split(","), registry)
    if getattr(args, "all_models", False):
        return get_model_keys(registry)
    saved = load_saved_selection(conn, row["notebook_id"])
    if saved:
        return validate_models(saved.get("selected_models", []), registry)
    if getattr(args, "no_auto_select", False):
        fallback = registry.get("model_policy", {}).get("fallback_models", ["structure_quick", "concept", "blindspot"])
        return validate_models(fallback, registry)
    selection = get_or_create_selection(conn, row, args, registry)
    return validate_models(selection.get("selected_models", []), registry)


# ============================================================
# 发现与匹配
# ============================================================


def get_notebooklm_notebooks() -> List[Dict[str, Any]]:
    rc, stdout, stderr = nblm("list", "--json", timeout=30)
    if rc != 0:
        print(f"❌ notebooklm list 失败: {stderr}")
        print("请先运行: notebooklm login")
        sys.exit(1)
    data = safe_json_loads(stdout, {})
    return data.get("notebooks", [])


def get_ebook_files() -> List[Path]:
    extensions = {".pdf", ".epub", ".mobi"}
    files: List[Path] = []
    if not PDF_DIR.exists():
        print(f"⚠️  PDF 目录不存在: {PDF_DIR}")
        return files
    for f in PDF_DIR.rglob("*"):
        if f.is_file() and f.suffix.lower() in extensions:
            files.append(f)
    return files


def match_notebooks_to_ebooks(notebooks: List[Dict[str, Any]], ebooks: List[Path]):
    import difflib
    matches = {}
    unmatched_notebooks = []
    used_ebooks = set()
    for nb in notebooks:
        title = nb.get("title", "")
        best_ratio = 0.0
        best_match = None
        for ebook in ebooks:
            if ebook in used_ebooks:
                continue
            ratio = difflib.SequenceMatcher(None, title.lower(), ebook.stem.lower()).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_match = ebook
        if best_ratio >= 0.8 and best_match:
            matches[nb["id"]] = (nb, best_match)
            used_ebooks.add(best_match)
        else:
            unmatched_notebooks.append((nb, best_match, best_ratio))
    return matches, unmatched_notebooks


def cmd_discover(args: argparse.Namespace) -> None:
    print("🔍 获取 NotebookLM 笔记本列表...")
    notebooks = get_notebooklm_notebooks()
    print(f"   找到 {len(notebooks)} 个笔记本")
    print("📚 扫描电子书文件...")
    ebooks = get_ebook_files()
    print(f"   找到 {len(ebooks)} 本电子书")
    print("🔗 模糊匹配...")
    matches, unmatched = match_notebooks_to_ebooks(notebooks, ebooks)
    conn = init_db()
    for notebook_id, (nb, ebook_path) in matches.items():
        conn.execute("""
            INSERT INTO books (notebook_id, notebook_title, ebook_filename, ebook_path, matched, updated_at)
            VALUES (?, ?, ?, ?, 1, datetime('now'))
            ON CONFLICT(notebook_id) DO UPDATE SET
                notebook_title=excluded.notebook_title,
                ebook_filename=excluded.ebook_filename,
                ebook_path=excluded.ebook_path,
                matched=1,
                updated_at=datetime('now')
        """, (notebook_id, nb.get("title", ""), ebook_path.name, str(ebook_path)))
    for nb, best, ratio in unmatched:
        conn.execute("""
            INSERT INTO books (notebook_id, notebook_title, matched, updated_at)
            VALUES (?, ?, 0, datetime('now'))
            ON CONFLICT(notebook_id) DO UPDATE SET notebook_title=excluded.notebook_title, matched=0, updated_at=datetime('now')
        """, (nb.get("id"), nb.get("title", "")))
    conn.commit()
    print(f"\n✅ 成功匹配: {len(matches)} 本")
    if unmatched:
        print(f"\n⚠️  未匹配的笔记本 ({len(unmatched)}):")
        for nb, best, ratio in unmatched:
            hint = f" 最接近: {best.stem} ({ratio:.0%})" if best else ""
            print(f"   - {nb.get('title', '')}{hint}")
    matched_ebooks = {str(ebook_path) for _, (_, ebook_path) in matches.items()}
    unmatched_ebooks = [e for e in ebooks if str(e) not in matched_ebooks]
    if unmatched_ebooks:
        print(f"\n⚠️  没有对应笔记本的电子书 ({len(unmatched_ebooks)}):")
        for e in unmatched_ebooks:
            print(f"   - {e.name}")
    conn.close()
    print("\n💡 下一步: python3 -u batch_generate.py select-models --book \"书名\"")


# ============================================================
# 模型推荐与选择
# ============================================================


def print_selection(selection: Dict[str, Any], registry: Dict[str, Any]) -> None:
    print(f"📘 书名: {selection.get('book_title')}")
    print(f"🔎 来源: {selection.get('source')}" + (f" / 搜索: {selection.get('search', {}).get('provider')}" if selection.get('search') else ""))
    print("\n✅ 推荐模型:")
    for m in selection.get("selected_models", []):
        print(f"  - {m}: {model_label(m, registry)}")
    print("\n🧠 选择理由:")
    print(selection.get("rationale", ""))
    if selection.get("llm_error"):
        print(f"\n⚠️  LLM 调用失败，已启用启发式兜底: {selection['llm_error']}")


def cmd_recommend(args: argparse.Namespace) -> None:
    registry = load_model_registry()
    selection = select_models_for_book(
        args.title,
        search_enabled=not args.no_search,
        force_search=args.force,
        min_models=args.min_models,
        max_models=args.max_models,
        use_llm=not args.no_llm,
    )
    print_selection(selection, registry)
    if args.json:
        print("\n--- JSON ---")
        print(json.dumps(selection, ensure_ascii=False, indent=2))


def cmd_select_models(args: argparse.Namespace) -> None:
    registry = load_model_registry()
    conn = init_db()
    book_where, book_params = where_book(args.book)
    sql = f"SELECT * FROM books WHERE matched=1{book_where} ORDER BY id"
    if args.limit > 0:
        sql += f" LIMIT {args.limit}"
    rows = conn.execute(sql, book_params).fetchall()
    if not rows:
        print("📭 没有匹配的书。请先运行 discover，或检查 --book 名称。")
        conn.close()
        return
    for row in rows:
        print("\n" + "=" * 70)
        selection = get_or_create_selection(conn, row, args, registry)
        print_selection(selection, registry)
    conn.close()
    print("\n✅ 模型选择完成。下一步可运行 generate / infographic / slides。")


# ============================================================
# NotebookLM artifact helpers
# ============================================================


def get_latest_artifact_id(notebook_id: str, artifact_type: str = "report") -> Optional[str]:
    rc, stdout, stderr = nblm("artifact", "list", "--notebook", notebook_id, "--type", artifact_type, "--json", timeout=30)
    if rc != 0:
        return None
    data = safe_json_loads(stdout, {})
    artifacts = data.get("artifacts", [])
    if artifacts:
        return artifacts[0].get("id")
    return None


def artifact_count(notebook_id: str, artifact_type: str) -> int:
    rc, stdout, _ = nblm("artifact", "list", "--notebook", notebook_id, "--type", artifact_type, "--json", timeout=30)
    if rc != 0:
        return 0
    return len(safe_json_loads(stdout, {}).get("artifacts", []))


def poll_artifact_complete(notebook_id: str, artifact_type: str, before_count: int, max_wait: int = 1200) -> Optional[Dict[str, Any]]:
    for _ in range(max_wait // 5):
        if interrupted:
            return None
        time.sleep(5)
        try:
            rc, stdout, _ = nblm("artifact", "list", "--notebook", notebook_id, "--type", artifact_type, "--json", timeout=30)
        except subprocess.TimeoutExpired:
            continue
        if rc != 0:
            continue
        arts = safe_json_loads(stdout, {}).get("artifacts", [])
        if len(arts) > before_count:
            newest = arts[0]
            if newest.get("status") in {"completed", "done", None}:
                return newest
    return None


# ============================================================
# 报告生成
# ============================================================


def generate_report(conn: sqlite3.Connection, notebook_id: str, notebook_title: str, model_key: str,
                    prompt: str, download_path: Path, registry: Dict[str, Any]) -> bool:
    label = model_label(model_key, registry)
    print(f"  📝 [{label}] 生成中...", end=" ", flush=True)
    update_run(conn, notebook_id, model_key, "generating")
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
        error_msg = f"生成失败 (exit={rc}): {stderr[:300] or stdout[:300]}"
        update_run(conn, notebook_id, model_key, "failed", error_log=error_msg)
        print(f"❌ {error_msg}")
        return False
    artifact_id = get_latest_artifact_id(notebook_id, "report")
    if not artifact_id:
        update_run(conn, notebook_id, model_key, "failed", error_log="无法获取 artifact_id")
        print("❌ 无法获取 artifact_id")
        return False
    print("✅ 下载...", end=" ", flush=True)
    rc, stdout, stderr = nblm("download", "report", str(download_path), "--notebook", notebook_id, "--latest", timeout=180)
    if rc == 0:
        update_run(conn, notebook_id, model_key, "done", artifact_id=artifact_id, output_path=str(download_path))
        print("✅")
        return True
    error_msg = f"下载失败: {stderr[:300] or stdout[:300]}"
    update_run(conn, notebook_id, model_key, "failed", artifact_id=artifact_id, error_log=error_msg)
    print(f"❌ {error_msg}")
    return False


def cmd_generate(args: argparse.Namespace) -> None:
    check_notebooklm_ready()
    registry = load_model_registry()
    conn = init_db()
    book_where, book_params = where_book(args.book)
    sql = f"SELECT * FROM books WHERE matched=1{book_where} ORDER BY id"
    if args.limit > 0:
        sql += f" LIMIT {args.limit}"
    rows = conn.execute(sql, book_params).fetchall()
    if not rows:
        print("📭 没有匹配的书。请先运行 discover。")
        conn.close()
        return
    print(f"📋 待处理: {len(rows)} 本书\n")
    for idx, row in enumerate(rows, start=1):
        if interrupted:
            break
        notebook_id = row["notebook_id"]
        notebook_title = row["notebook_title"]
        ebook_path = row["ebook_path"]
        base_name = Path(ebook_path).stem if ebook_path else notebook_title
        output_dir = ensure_output_dir(base_name)
        models = models_for_work(conn, row, args, registry)
        print(f"\n📖 [{idx}/{len(rows)}] {notebook_title}")
        print("   模型: " + ", ".join(f"{m}({model_label(m, registry)})" for m in models))
        for model_key in models:
            if interrupted:
                break
            status = get_run_status(conn, notebook_id, model_key)
            output_path = model_report_path(base_name, output_dir, model_key, registry)
            if output_path.exists() and not args.force:
                if status != "done":
                    update_run(conn, notebook_id, model_key, "done", output_path=str(output_path))
                print(f"  ⏭️  [{model_label(model_key, registry)}]: 文件已存在，跳过")
                continue
            if status == "done" and not args.force:
                print(f"  ⏭️  [{model_label(model_key, registry)}]: 已完成，跳过")
                continue
            if status == "failed" and not args.retry_failed and not args.force:
                print(f"  ⏭️  [{model_label(model_key, registry)}]: 上次失败，使用 --retry-failed 重试")
                continue
            prompt = load_prompt_for_model(model_key, registry)
            generate_report(conn, notebook_id, notebook_title, model_key, prompt, output_path, registry)
            time.sleep(2)
        time.sleep(5)
    conn.close()
    print("\n🏁 报告生成流程完成")


# ============================================================
# 信息图、PPT、思维导图、闪卡、测验
# ============================================================


def cmd_infographic(args: argparse.Namespace) -> None:
    check_notebooklm_ready()
    registry = load_model_registry()
    if args.style not in INFOGRAPHIC_STYLES:
        print(f"❌ 未知风格: {args.style}")
        print(f"可选: {', '.join(INFOGRAPHIC_STYLES)}")
        sys.exit(1)
    conn = init_db()
    book_where, book_params = where_book(args.book)
    sql = f"SELECT * FROM books WHERE matched=1{book_where} ORDER BY id"
    if args.limit > 0:
        sql += f" LIMIT {args.limit}"
    rows = conn.execute(sql, book_params).fetchall()
    if not rows:
        print("📭 没有匹配的书。")
        conn.close()
        return
    for row in rows:
        if interrupted:
            break
        notebook_id = row["notebook_id"]
        notebook_title = row["notebook_title"]
        base_name = Path(row["ebook_path"]).stem if row["ebook_path"] else notebook_title
        output_dir = ensure_output_dir(base_name)
        models = models_for_work(conn, row, args, registry)
        print(f"\n📖 {notebook_title}")
        for model_key in models:
            if interrupted:
                break
            if get_run_status(conn, notebook_id, model_key) != "done" and not args.force:
                print(f"  ⏭️  [{model_label(model_key, registry)} 信息图]: 报告未完成，跳过")
                continue
            output_path = model_infographic_path(base_name, output_dir, model_key, registry)
            if output_path.exists() and not args.force:
                print(f"  ⏭️  [{model_label(model_key, registry)} 信息图]: 已存在，跳过")
                continue
            prompt = registry["models"][model_key].get("infographic_prompt") or f"基于{model_label(model_key, registry)}视角，为本书生成中文信息图。"
            print(f"  🎨 [{model_label(model_key, registry)} 信息图] 生成中...", end=" ", flush=True)
            rc, stdout, stderr = nblm_with_retry(
                "generate", "infographic", prompt,
                "--notebook", notebook_id,
                "--language", "zh_Hans",
                "--style", args.style,
                "--wait",
                "--json",
                timeout=900,
            )
            if rc != 0:
                print(f"❌ 生成失败: {stderr[:150] or stdout[:150]}")
                continue
            print("✅ 下载...", end=" ", flush=True)
            rc, stdout, stderr = nblm("download", "infographic", str(output_path), "--notebook", notebook_id, "--latest", timeout=180)
            print("✅" if rc == 0 else f"❌ 下载失败: {stderr[:100]}")
            time.sleep(2)
    conn.close()
    print("\n🏁 信息图生成流程完成")


def cmd_slides(args: argparse.Namespace) -> None:
    check_notebooklm_ready()
    registry = load_model_registry()
    conn = init_db()
    book_where, book_params = where_book(args.book)
    sql = f"SELECT * FROM books WHERE matched=1{book_where} ORDER BY id"
    if args.limit > 0:
        sql += f" LIMIT {args.limit}"
    rows = conn.execute(sql, book_params).fetchall()
    if not rows:
        print("📭 没有匹配的书。")
        conn.close()
        return
    for row in rows:
        if interrupted:
            break
        notebook_id = row["notebook_id"]
        notebook_title = row["notebook_title"]
        base_name = Path(row["ebook_path"]).stem if row["ebook_path"] else notebook_title
        output_dir = ensure_output_dir(base_name)
        models = models_for_work(conn, row, args, registry)
        print(f"\n📖 {notebook_title}")
        for model_key in models:
            if interrupted:
                break
            if get_run_status(conn, notebook_id, model_key) != "done" and not args.force:
                print(f"  ⏭️  [{model_label(model_key, registry)} 演示文稿]: 报告未完成，跳过")
                continue
            output_path = model_slides_path(base_name, output_dir, model_key, registry)
            if output_path.exists() and not args.force:
                print(f"  ⏭️  [{model_label(model_key, registry)} 演示文稿]: 已存在，跳过")
                continue
            prompt = registry["models"][model_key].get("slide_prompt") or f"基于{model_label(model_key, registry)}视角，为本书创建中文演示文稿。"
            before = artifact_count(notebook_id, "slide-deck")
            print(f"  📊 [{model_label(model_key, registry)} 演示文稿] 触发生成...", end=" ", flush=True)
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
                print(f"❌ 启动失败: {stderr[:120] or stdout[:120]}")
                continue
            print("轮询中...", end=" ", flush=True)
            artifact = poll_artifact_complete(notebook_id, "slide-deck", before, max_wait=1200)
            if not artifact:
                print("❌ 超时")
                continue
            rc, stdout, stderr = nblm("download", "slide-deck", str(output_path), "--notebook", notebook_id, "--artifact", artifact["id"], "--format", "pptx", timeout=300)
            print(f"✅ ({output_path.stat().st_size // 1024}KB)" if rc == 0 else f"❌ 下载失败: {stderr[:100]}")
            time.sleep(2)
    conn.close()
    print("\n🏁 演示文稿生成流程完成")


def cmd_mindmap(args: argparse.Namespace) -> None:
    check_notebooklm_ready()
    conn = init_db()
    book_where, book_params = where_book(args.book)
    sql = f"SELECT * FROM books WHERE matched=1{book_where} ORDER BY id"
    if args.limit > 0:
        sql += f" LIMIT {args.limit}"
    rows = conn.execute(sql, book_params).fetchall()
    if not rows:
        print("📭 没有匹配的书。")
        conn.close()
        return
    for row in rows:
        if interrupted:
            break
        notebook_id = row["notebook_id"]
        title = row["notebook_title"]
        base_name = Path(row["ebook_path"]).stem if row["ebook_path"] else title
        output_dir = ensure_output_dir(base_name)
        output_path = output_dir / f"{base_name}_思维导图.json"
        if output_path.exists() and not args.force:
            print(f"⏭️  [{title}] 思维导图已存在，跳过")
            continue
        print(f"🧠 [{title}] 生成思维导图...", end=" ", flush=True)
        rc, stdout, stderr = nblm_with_retry("generate", "mind-map", "--notebook", notebook_id, "--json", timeout=900)
        if rc != 0:
            print(f"❌ 生成失败: {stderr[:120] or stdout[:120]}")
            continue
        rc, stdout, stderr = nblm("download", "mind-map", str(output_path), "--notebook", notebook_id, "--latest", timeout=180)
        print("✅" if rc == 0 else f"❌ 下载失败: {stderr[:100]}")
    conn.close()


def cmd_flashcards(args: argparse.Namespace) -> None:
    check_notebooklm_ready()
    conn = init_db()
    book_where, book_params = where_book(args.book)
    sql = f"SELECT * FROM books WHERE matched=1{book_where} ORDER BY id"
    if args.limit > 0:
        sql += f" LIMIT {args.limit}"
    rows = conn.execute(sql, book_params).fetchall()
    if not rows:
        print("📭 没有匹配的书。")
        conn.close()
        return
    for row in rows:
        if interrupted:
            break
        notebook_id = row["notebook_id"]
        title = row["notebook_title"]
        base_name = Path(row["ebook_path"]).stem if row["ebook_path"] else title
        output_dir = ensure_output_dir(base_name)
        output_path = output_dir / f"{base_name}_闪卡.md"
        if output_path.exists() and not args.force:
            print(f"⏭️  [{title}] 闪卡已存在，跳过")
            continue
        before = artifact_count(notebook_id, "flashcard")
        print(f"🃏 [{title}] 生成闪卡...", end=" ", flush=True)
        rc, stdout, stderr = nblm("generate", "flashcards", FLASHCARD_PROMPT, "--notebook", notebook_id, "--no-wait", "--json", timeout=30)
        if rc != 0:
            print(f"❌ 启动失败: {stderr[:100] or stdout[:100]}")
            continue
        artifact = poll_artifact_complete(notebook_id, "flashcard", before, max_wait=600)
        if not artifact:
            print("❌ 超时")
            continue
        rc, stdout, stderr = nblm("download", "flashcards", str(output_path), "--notebook", notebook_id, "--artifact", artifact["id"], "--format", "markdown", timeout=180)
        print("✅" if rc == 0 else f"❌ 下载失败: {stderr[:100]}")
    conn.close()


def cmd_quiz(args: argparse.Namespace) -> None:
    check_notebooklm_ready()
    conn = init_db()
    difficulties = [args.difficulty] if args.difficulty else ["easy", "medium", "hard"]
    labels = {"easy": "简单", "medium": "中等", "hard": "困难"}
    book_where, book_params = where_book(args.book)
    sql = f"SELECT * FROM books WHERE matched=1{book_where} ORDER BY id"
    if args.limit > 0:
        sql += f" LIMIT {args.limit}"
    rows = conn.execute(sql, book_params).fetchall()
    if not rows:
        print("📭 没有匹配的书。")
        conn.close()
        return
    for row in rows:
        if interrupted:
            break
        notebook_id = row["notebook_id"]
        title = row["notebook_title"]
        base_name = Path(row["ebook_path"]).stem if row["ebook_path"] else title
        output_dir = ensure_output_dir(base_name)
        print(f"📖 {title}")
        for diff in difficulties:
            output_path = output_dir / f"{base_name}_测验_{labels[diff]}.md"
            if output_path.exists() and not args.force:
                print(f"  ⏭️  测验({labels[diff]}) 已存在，跳过")
                continue
            before = artifact_count(notebook_id, "quiz")
            print(f"  📝 测验({labels[diff]}) 生成中...", end=" ", flush=True)
            rc, stdout, stderr = nblm("generate", "quiz", QUIZ_PROMPTS[diff], "--notebook", notebook_id, "--difficulty", diff, "--no-wait", "--json", timeout=30)
            if rc != 0:
                print(f"❌ 启动失败: {stderr[:100] or stdout[:100]}")
                continue
            artifact = poll_artifact_complete(notebook_id, "quiz", before, max_wait=600)
            if not artifact:
                print("❌ 超时")
                continue
            rc, stdout, stderr = nblm("download", "quiz", str(output_path), "--notebook", notebook_id, "--artifact", artifact["id"], "--format", "markdown", timeout=180)
            print("✅" if rc == 0 else f"❌ 下载失败: {stderr[:100]}")
    conn.close()


# ============================================================
# 状态
# ============================================================


def cmd_status(args: argparse.Namespace) -> None:
    registry = load_model_registry()
    conn = init_db()
    book_where, book_params = where_book(args.book)
    rows = conn.execute(f"SELECT * FROM books WHERE 1=1{book_where} ORDER BY id", book_params).fetchall()
    if not rows:
        print("📭 暂无书籍记录。")
        conn.close()
        return
    print(f"📊 共 {len(rows)} 本书\n")
    for row in rows:
        title = row["notebook_title"]
        matched = "✅" if row["matched"] else "❌"
        selection = load_saved_selection(conn, row["notebook_id"])
        selected = selection.get("selected_models", []) if selection else []
        print(f"{matched} {title}")
        if row["ebook_path"]:
            print(f"   文件: {Path(row['ebook_path']).name}")
        run_rows = conn.execute("SELECT model_key, status, output_path FROM model_runs WHERE notebook_id=? ORDER BY model_key", (row["notebook_id"],)).fetchall()
        if selected:
            print("   已选模型: " + ", ".join(f"{m}({model_label(m, registry)})" for m in selected))
            for rr in run_rows:
                if rr["model_key"] in selected:
                    icon = {"pending": "⏳", "generating": "📝", "done": "✅", "failed": "❌"}.get(rr["status"], "•")
                    print(f"      {icon} {rr['model_key']}: {rr['status']}" + (f" -> {Path(rr['output_path']).name}" if rr["output_path"] else ""))
            legacy_done = [rr for rr in run_rows if rr["model_key"] not in selected and rr["status"] == "done"]
            if legacy_done:
                print("   历史已完成但本次未选模型: " + ", ".join(rr["model_key"] for rr in legacy_done))
        else:
            print("   已选模型: 未选择（运行 select-models 或 generate 时自动选择）")
            if run_rows:
                done = [rr["model_key"] for rr in run_rows if rr["status"] == "done"]
                if done:
                    print("   历史已完成模型: " + ", ".join(done))
        print()
    conn.close()


# ============================================================
# CLI
# ============================================================


def add_common_book_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--book", type=str, help="按书名过滤，支持模糊匹配")
    parser.add_argument("--limit", type=int, default=0, help="最多处理 N 本")


def add_selection_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--force-select", action="store_true", help="忽略缓存，重新搜索并选择模型")
    parser.add_argument("--no-search", action="store_true", help="不做网络检索，只按书名/LLM/启发式判断")
    parser.add_argument("--no-llm", action="store_true", help="不调用 DeepSeek，只用启发式规则")
    parser.add_argument("--min-models", type=int, default=None, help="最少选择几个模型")
    parser.add_argument("--max-models", type=int, default=None, help="最多选择几个模型")


def add_model_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--only", type=str, help="只处理指定模型，逗号分隔，例如: structure_quick,causal,blindspot")
    parser.add_argument("--all-models", action="store_true", help="强制处理全部 11 个模型（不推荐日常使用）")
    parser.add_argument("--no-auto-select", action="store_true", help="没有已选模型时，不自动选择，改用 fallback_models")
    add_selection_args(parser)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="电子书批量笔记生成系统 — 按书名自适应选择阅读模型",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
推荐流程:
  python3 -u batch_generate.py discover
  python3 -u batch_generate.py select-models --book "大国大城"
  python3 -u batch_generate.py generate --book "大国大城"
  python3 -u batch_generate.py infographic --book "大国大城"
  python3 -u batch_generate.py slides --book "大国大城"

单独测试书名推荐:
  python3 -u batch_generate.py recommend --title "置身事内"
  python3 -u batch_generate.py recommend --title "原则" --no-search --no-llm
        """,
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("discover", help="发现并匹配 NotebookLM 笔记本和本地电子书")

    rec = sub.add_parser("recommend", help="只根据输入书名推荐分析模型，不依赖 NotebookLM")
    rec.add_argument("--title", required=True, help="书名")
    rec.add_argument("--json", action="store_true", help="同时输出完整 JSON")
    rec.add_argument("--force", action="store_true", help="忽略缓存重新搜索和推荐")
    rec.add_argument("--no-search", action="store_true", help="不做网络检索")
    rec.add_argument("--no-llm", action="store_true", help="不调用 DeepSeek")
    rec.add_argument("--min-models", type=int, default=None)
    rec.add_argument("--max-models", type=int, default=None)

    sel = sub.add_parser("select-models", help="为已 discover 的书籍选择并保存合适模型")
    add_common_book_args(sel)
    add_selection_args(sel)

    gen = sub.add_parser("generate", help="按已选模型生成分析报告")
    add_common_book_args(gen)
    add_model_args(gen)
    gen.add_argument("--retry-failed", action="store_true", help="重试失败项")
    gen.add_argument("--force", action="store_true", help="覆盖已完成/已存在报告")

    info = sub.add_parser("infographic", help="按已选模型为完成报告生成信息图")
    add_common_book_args(info)
    add_model_args(info)
    info.add_argument("--style", type=str, default="sketch-note", help=f"信息图风格: {', '.join(INFOGRAPHIC_STYLES)}")
    info.add_argument("--force", action="store_true", help="覆盖已有文件")

    slides = sub.add_parser("slides", help="按已选模型为完成报告生成演示文稿")
    add_common_book_args(slides)
    add_model_args(slides)
    slides.add_argument("--force", action="store_true", help="覆盖已有文件")

    mm = sub.add_parser("mindmap", help="生成全书思维导图")
    add_common_book_args(mm)
    mm.add_argument("--force", action="store_true", help="覆盖已有文件")

    fc = sub.add_parser("flashcards", help="生成关键概念闪卡")
    add_common_book_args(fc)
    fc.add_argument("--force", action="store_true", help="覆盖已有文件")

    quiz = sub.add_parser("quiz", help="生成测验题")
    add_common_book_args(quiz)
    quiz.add_argument("--difficulty", type=str, choices=["easy", "medium", "hard"], help="只生成指定难度")
    quiz.add_argument("--force", action="store_true", help="覆盖已有文件")

    status = sub.add_parser("status", help="查看模型选择和生成进度")
    status.add_argument("--book", type=str, help="按书名过滤")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    PDF_DIR.mkdir(parents=True, exist_ok=True)

    if args.command == "discover":
        cmd_discover(args)
    elif args.command == "recommend":
        cmd_recommend(args)
    elif args.command == "select-models":
        cmd_select_models(args)
    elif args.command == "generate":
        cmd_generate(args)
    elif args.command == "infographic":
        cmd_infographic(args)
    elif args.command == "slides":
        cmd_slides(args)
    elif args.command == "mindmap":
        cmd_mindmap(args)
    elif args.command == "flashcards":
        cmd_flashcards(args)
    elif args.command == "quiz":
        cmd_quiz(args)
    elif args.command == "status":
        cmd_status(args)


if __name__ == "__main__":
    main()
