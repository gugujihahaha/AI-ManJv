"""Repository functions keep SQL out of API handlers."""

import uuid
from datetime import UTC, datetime

from app.database import connection
from app.schemas import ProjectCreate, StoryboardCreate, StoryboardUpdate


def _now() -> str:
    return datetime.now(UTC).isoformat()


def list_projects() -> list[dict]:
    with connection() as db:
        rows = db.execute("""SELECT p.*, COUNT(s.id) AS storyboard_count FROM projects p
            LEFT JOIN storyboards s ON s.project_id = p.id GROUP BY p.id ORDER BY p.updated_at DESC""").fetchall()
    return [dict(row) for row in rows]


def create_project(payload: ProjectCreate) -> dict:
    project_id, now = str(uuid.uuid4()), _now()
    with connection() as db:
        db.execute("""INSERT INTO projects
            (id,title,story_text,art_style,voice,target_duration,status,error_message,created_at,updated_at)
            VALUES (?,?,?,?,?,?,'draft','',?,?)""",
            (project_id, payload.title.strip(), payload.story_text, payload.art_style,
             payload.voice, payload.target_duration, now, now))
    return get_project(project_id)


def get_project(project_id: str) -> dict | None:
    with connection() as db:
        project = db.execute("""SELECT p.*, COUNT(s.id) AS storyboard_count FROM projects p
            LEFT JOIN storyboards s ON s.project_id=p.id WHERE p.id=? GROUP BY p.id""", (project_id,)).fetchone()
        if not project:
            return None
        rows = db.execute("SELECT * FROM storyboards WHERE project_id=? ORDER BY sequence", (project_id,)).fetchall()
    result = dict(project)
    result["storyboards"] = [dict(row) for row in rows]
    return result


def replace_storyboards(project_id: str, items: list[StoryboardCreate]) -> dict:
    now = _now()
    with connection() as db:
        db.execute("DELETE FROM storyboards WHERE project_id=?", (project_id,))
        db.executemany("""INSERT INTO storyboards
            (id,project_id,sequence,scene_description,dialogue,emotion,duration,image_prompt,status,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,'draft',?,?)""",
            [(str(uuid.uuid4()), project_id, i.sequence, i.scene_description, i.dialogue,
              i.emotion, i.duration, i.image_prompt, now, now) for i in items])
        db.execute("UPDATE projects SET status='storyboard_ready',error_message='',updated_at=? WHERE id=?", (now, project_id))
    return get_project(project_id)


def get_storyboard(storyboard_id: str) -> dict | None:
    with connection() as db:
        row = db.execute("SELECT * FROM storyboards WHERE id=?", (storyboard_id,)).fetchone()
    return dict(row) if row else None


def update_storyboard(storyboard_id: str, payload: StoryboardUpdate) -> dict | None:
    changes = payload.model_dump(exclude_none=True)
    if not changes:
        return get_storyboard(storyboard_id)
    changes["updated_at"] = _now()
    assignments = ", ".join(f"{key}=?" for key in changes)
    with connection() as db:
        cursor = db.execute(f"UPDATE storyboards SET {assignments} WHERE id=?", (*changes.values(), storyboard_id))
        if not cursor.rowcount:
            return None
    return get_storyboard(storyboard_id)


def set_project_status(project_id: str, status: str, error_message: str = "") -> None:
    with connection() as db:
        db.execute("UPDATE projects SET status=?,error_message=?,updated_at=? WHERE id=?",
                   (status, error_message, _now(), project_id))


def confirm_storyboards(project_id: str) -> dict | None:
    now = _now()
    with connection() as db:
        count = db.execute("SELECT COUNT(*) FROM storyboards WHERE project_id=?", (project_id,)).fetchone()[0]
        if count < 3:
            return None
        db.execute("UPDATE storyboards SET status='confirmed',updated_at=? WHERE project_id=?", (now, project_id))
        db.execute("UPDATE projects SET status='storyboard_confirmed',updated_at=? WHERE id=?", (now, project_id))
    return get_project(project_id)


def delete_project(project_id: str) -> bool:
    with connection() as db:
        cursor = db.execute("DELETE FROM projects WHERE id=?", (project_id,))
    return bool(cursor.rowcount)
