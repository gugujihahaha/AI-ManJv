"""Small SQLite persistence layer for the single-user MVP."""

import sqlite3
from contextlib import contextmanager
from typing import Iterator

from app.config import settings


SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    story_text TEXT NOT NULL,
    art_style TEXT NOT NULL,
    voice TEXT NOT NULL,
    target_duration INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    error_message TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS storyboards (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL,
    scene_description TEXT NOT NULL,
    dialogue TEXT NOT NULL DEFAULT '',
    emotion TEXT NOT NULL DEFAULT '平静',
    duration REAL NOT NULL DEFAULT 3,
    image_prompt TEXT NOT NULL DEFAULT '',
    image_path TEXT,
    status TEXT NOT NULL DEFAULT 'draft',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(project_id, sequence)
);
CREATE INDEX IF NOT EXISTS idx_storyboards_project_sequence
ON storyboards(project_id, sequence);
"""


@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(settings.database_path, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def initialize_database() -> None:
    with connection() as db:
        db.executescript(SCHEMA)
