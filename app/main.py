"""FastAPI application factory."""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.routes import router
from app.config import Settings, load_settings
from app.db import create_db_engine, create_session_factory, init_db
from app.errors import DomainError
from app.seed import load_seed
from app.service import RegistrationService


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = create_db_engine(settings.database_url)
        init_db(engine)
        session_factory = create_session_factory(engine)
        if settings.seed_file is not None and settings.seed_file.exists():
            load_seed(session_factory, settings.seed_file)
        app.state.service = RegistrationService(session_factory)
        yield
        engine.dispose()

    app = FastAPI(title="Course Waitlist Service", version="0.1.0", lifespan=lifespan)

    @app.exception_handler(DomainError)
    async def _domain_error(_request: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.http_status,
            content={"error": {"code": exc.code, "message": exc.message, "details": exc.details}},
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        # Same envelope as domain errors so clients handle a single shape.
        details = [{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()]
        return JSONResponse(
            status_code=422,
            content={"error": {"code": "INVALID_REQUEST", "message": "Request validation failed.", "details": {"errors": details}}},
        )

    app.include_router(router)
    return app


app = create_app()
