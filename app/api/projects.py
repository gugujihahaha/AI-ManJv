"""Project workflow endpoints."""

from fastapi import APIRouter, HTTPException, Response, status

from app import repository
from app.schemas import (GenerateStoryboardsRequest, ManualStoryboardsRequest, ProjectCreate,
                         ProjectDetail, ProjectSummary, Storyboard, StoryboardUpdate)
from app.services.storyboard import StoryboardGenerationError, demo_storyboards, generate_storyboards


router = APIRouter(prefix="/api", tags=["漫剧项目"])


def _project_or_404(project_id: str) -> dict:
    project = repository.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="作品不存在")
    return project


@router.get("/projects", response_model=list[ProjectSummary])
def projects() -> list[dict]:
    return repository.list_projects()


@router.post("/projects", response_model=ProjectDetail, status_code=status.HTTP_201_CREATED)
def create_project(payload: ProjectCreate) -> dict:
    return repository.create_project(payload)


@router.get("/projects/{project_id}", response_model=ProjectDetail)
def project_detail(project_id: str) -> dict:
    return _project_or_404(project_id)


@router.delete("/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(project_id: str) -> Response:
    if not repository.delete_project(project_id):
        raise HTTPException(status_code=404, detail="作品不存在")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/projects/{project_id}/storyboards/generate", response_model=ProjectDetail)
def generate(project_id: str, payload: GenerateStoryboardsRequest) -> dict:
    project = _project_or_404(project_id)
    if project["status"] == "storyboard_confirmed":
        raise HTTPException(status_code=409, detail="分镜已经确认，不能整体覆盖")
    repository.set_project_status(project_id, "storyboard_generating")
    try:
        items = (demo_storyboards if payload.mode == "demo" else generate_storyboards)(
            project["story_text"], project["art_style"], project["target_duration"])
        return repository.replace_storyboards(project_id, items)
    except StoryboardGenerationError as exc:
        repository.set_project_status(project_id, "storyboard_failed", str(exc))
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.put("/projects/{project_id}/storyboards/manual", response_model=ProjectDetail)
def manual_storyboards(project_id: str, payload: ManualStoryboardsRequest) -> dict:
    project = _project_or_404(project_id)
    if project["status"] == "storyboard_confirmed":
        raise HTTPException(status_code=409, detail="分镜已经确认，不能整体覆盖")
    return repository.replace_storyboards(project_id, payload.storyboards)


@router.patch("/storyboards/{storyboard_id}", response_model=Storyboard)
def edit_storyboard(storyboard_id: str, payload: StoryboardUpdate) -> dict:
    existing = repository.get_storyboard(storyboard_id)
    if not existing:
        raise HTTPException(status_code=404, detail="分镜不存在")
    project = _project_or_404(existing["project_id"])
    if project["status"] == "storyboard_confirmed":
        raise HTTPException(status_code=409, detail="已确认分镜不可再编辑")
    return repository.update_storyboard(storyboard_id, payload)


@router.post("/projects/{project_id}/storyboards/confirm", response_model=ProjectDetail)
def confirm(project_id: str) -> dict:
    _project_or_404(project_id)
    project = repository.confirm_storyboards(project_id)
    if not project:
        raise HTTPException(status_code=409, detail="至少需要 3 个分镜才能确认")
    return project
