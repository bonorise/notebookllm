#!/usr/bin/env python3
"""Migrate old books.db status columns to the adaptive 11-model workflow schema.

Usage:
  python3 migrate_books_db.py --db books.db
  python3 migrate_books_db.py --db /path/to/books.db --no-backup

The migration is safe and idempotent:
- It keeps the original books table and legacy columns.
- It creates model_selections and model_runs for the new workflow.
- It copies old axiom/bayes/first_principles/dialectic statuses into model_runs.
- It preserves old academic records in legacy_academic_runs instead of mapping them to the new structure model.
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    return {row[1] for row in conn.execute(f'PRAGMA table_info("{table_name}")').fetchall()}


def ensure_new_tables(conn: sqlite3.Connection) -> None:
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
    conn.commit()


def migrate(conn: sqlite3.Connection) -> dict[str, int]:
    ensure_new_tables(conn)
    cols = table_columns(conn, "books")
    if not {"notebook_id", "notebook_title"}.issubset(cols):
        raise RuntimeError("books 表缺少 notebook_id/notebook_title 字段，无法迁移。")

    rows = conn.execute("SELECT * FROM books").fetchall()
    legacy_map = {
        "axiom": ("axiom_status", "axiom_artifact_id", "axiom_path"),
        "bayes": ("bayes_status", "bayes_artifact_id", "bayes_path"),
        "first_principles": ("first_principles_status", "first_principles_artifact_id", "first_principles_path"),
        "dialectic": ("dialectic_status", "dialectic_artifact_id", "dialectic_path"),
    }

    migrated_runs = 0
    for row in rows:
        notebook_id = row["notebook_id"]
        for model_key, (status_col, artifact_col, path_col) in legacy_map.items():
            if status_col not in cols:
                continue
            status = row[status_col]
            artifact_id = row[artifact_col] if artifact_col in cols else None
            output_path = row[path_col] if path_col in cols else None
            if not status and not artifact_id and not output_path:
                continue
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
            """, (notebook_id, model_key, status or "pending", artifact_id, output_path))
            migrated_runs += 1

    migrated_academic = 0
    if "academic_status" in cols:
        for row in rows:
            status = row["academic_status"]
            artifact_id = row["academic_artifact_id"] if "academic_artifact_id" in cols else None
            output_path = row["academic_path"] if "academic_path" in cols else None
            if not status and not artifact_id and not output_path:
                continue
            conn.execute("""
                INSERT INTO legacy_academic_runs (notebook_id, status, artifact_id, output_path, updated_at)
                VALUES (?, ?, ?, ?, datetime('now'))
                ON CONFLICT(notebook_id) DO UPDATE SET
                    status=excluded.status,
                    artifact_id=COALESCE(excluded.artifact_id, legacy_academic_runs.artifact_id),
                    output_path=COALESCE(excluded.output_path, legacy_academic_runs.output_path),
                    updated_at=datetime('now')
            """, (row["notebook_id"], status, artifact_id, output_path))
            migrated_academic += 1

    conn.commit()
    return {
        "books": len(rows),
        "model_runs_migrated": migrated_runs,
        "legacy_academic_migrated": migrated_academic,
        "model_runs_total": conn.execute("SELECT COUNT(*) FROM model_runs").fetchone()[0],
        "model_selections_total": conn.execute("SELECT COUNT(*) FROM model_selections").fetchone()[0],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="迁移旧版 books.db 到 11 模型自适应流程可用结构")
    parser.add_argument("--db", default="books.db", help="books.db 路径，默认当前目录 books.db")
    parser.add_argument("--no-backup", action="store_true", help="不创建备份")
    args = parser.parse_args()

    db_path = Path(args.db).expanduser().resolve()
    if not db_path.exists():
        raise SystemExit(f"❌ 未找到数据库：{db_path}")

    if not args.no_backup:
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_path = db_path.with_suffix(db_path.suffix + f".bak-{ts}")
        shutil.copy2(db_path, backup_path)
        print(f"✅ 已备份：{backup_path}")

    conn = connect(db_path)
    try:
        stats = migrate(conn)
    finally:
        conn.close()

    print("✅ 迁移完成")
    for k, v in stats.items():
        print(f"- {k}: {v}")
    print("\n下一步可运行：")
    print("  python3 batch_generate.py status")
    print("  python3 batch_generate.py select-models --book \"书名\"")


if __name__ == "__main__":
    main()
