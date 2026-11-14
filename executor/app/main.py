import mimetypes
from contextlib import asynccontextmanager
from pathlib import Path

from app.config import Settings, get_settings
from app.errors import ToolError
from app.log_redaction import install_log_redaction
from app.models import ExecuteRequest, ToolResult
from app.security import require_bearer_token
from app.service import ExecutorService
from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse

from app import __version__


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await app.state.service.close()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    install_log_redaction(settings.token, settings.log_level)
    application = FastAPI(
        title="Rana Agent Executor",
        version=__version__,
        description="按 task_id 隔离的内部工具执行服务",
        lifespan=lifespan,
    )
    application.state.settings = settings
    application.state.service = ExecutorService(settings)

    @application.get("/healthz", tags=["system"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @application.post(
        "/api/v1/execute",
        response_model=ToolResult,
        dependencies=[Depends(require_bearer_token)],
        tags=["executor"],
    )
    async def execute(request: ExecuteRequest) -> ToolResult:
        return await application.state.service.execute(request)

    @application.get(
        "/api/v1/artifacts/{task_id}/{path:path}",
        dependencies=[Depends(require_bearer_token)],
        tags=["executor"],
    )
    async def download_artifact(task_id: str, path: str) -> FileResponse:
        service: ExecutorService = application.state.service
        try:
            target, _, normalized = service.workspace.resolve(task_id, path)
            if not target.exists() or not target.is_file():
                raise ToolError("FILE_NOT_FOUND", "产物不存在", {"path": normalized})
        except ToolError as exc:
            status_code = 404 if exc.code == "FILE_NOT_FOUND" else 400
            raise HTTPException(status_code=status_code, detail=exc.as_body().model_dump()) from exc
        media_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        return FileResponse(
            path=Path(target),
            media_type=media_type,
            filename=target.name,
            content_disposition_type="attachment",
        )

    return application


app = create_app()
