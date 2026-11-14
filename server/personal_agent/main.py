from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import include_rest_routes
from .api.audio import register_audio_routes
from .api.voice import register_voice_routes
from .bootstrap import bootstrap_identity, ensure_persistent_secret
from .config import Settings, get_settings
from .connectors.service import ConnectorToolExecutor
from .db import dispose_database, init_database, make_engine, make_session_factory
from .health import router as health_router
from .observability import RequestContextMiddleware, configure_logging
from .providers.factory import create_ai_provider
from .services.executor import ExecutorClient
from .services.scheduler import TaskScheduler
from .services.tasks import TaskManager


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        ensure_persistent_secret(settings)
        engine = make_engine(settings.db_url)
        await init_database(engine)
        app.state.engine = engine
        app.state.session_factory = make_session_factory(engine)
        app.state.settings = settings
        await bootstrap_identity(app.state.session_factory, settings)
        provider = create_ai_provider(settings)
        executor = ExecutorClient(
            settings.executor_base_url,
            settings.executor_token,
            timeout_seconds=settings.executor_timeout_seconds,
            max_retries=settings.max_retries,
        )
        tool_executor = ConnectorToolExecutor(app.state.session_factory, executor)
        manager = TaskManager(
            app.state.session_factory, provider, executor=tool_executor, settings=settings
        )
        scheduler = TaskScheduler(
            app.state.session_factory,
            manager,
            events=getattr(manager, "events", None),
            interval_seconds=settings.scheduler_interval_seconds,
        )
        app.state.ai_provider = provider
        app.state.mimo_provider = provider
        app.state.executor = executor
        app.state.tool_executor = tool_executor
        app.state.task_manager = manager
        app.state.scheduler = scheduler
        await manager.start()
        await scheduler.start()
        try:
            yield
        finally:
            await scheduler.stop()
            await manager.stop()
            await manager.events.close()
            await executor.close()
            await provider.close()
            await dispose_database(engine)

    application = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description="NAS 上持续运行、支持 MiMo / MiniMax 语音的个人 Agent",
        lifespan=lifespan,
    )
    application.add_middleware(RequestContextMiddleware)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=settings.cors_origin_list != ["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.include_router(health_router)
    include_rest_routes(application, prefix="/api/v1")
    register_audio_routes(application)
    register_voice_routes(application)
    return application


app = create_app()
