from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from app.config import settings
from app.database import Base, engine
# from app.schema_patches import apply_postgres_schema_patches
from app.routers import auth, games, groups, me


@asynccontextmanager
async def lifespan(_app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # await apply_postgres_schema_patches(conn)
    yield


app = FastAPI(
    title="Sattaler API",
    description=(
        "REST API for Sattaler.\n\n"
        "- **Swagger UI:** [`/docs`](/docs)\n"
        "- **ReDoc:** [`/redoc`](/redoc)\n"
        "- **OpenAPI JSON:** [`/openapi.json`](/openapi.json)\n\n"
        "Call `POST /auth/google`, copy `access_token`, then **Authorize** → "
        "HTTP Bearer (paste token only, no `Bearer ` prefix)."
    ),
    version="0.1.0",
    lifespan=lifespan,
    openapi_url="/openapi.json",
    docs_url="/docs",
    redoc_url="/redoc",
    swagger_ui_parameters={"persistAuthorization": True},
    openapi_tags=[
        {"name": "auth", "description": "Google sign-in and session"},
        {"name": "groups", "description": "Player groups (JWT required)"},
        {"name": "games", "description": "Saved games per group (JWT required)"},
        {"name": "me", "description": "Current user stats (JWT required)"},
    ],
)

if settings.cors_origins.strip() == "*":
    allow_origins = ["*"]
    allow_credentials = False
else:
    allow_origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
    allow_credentials = True

app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_credentials=allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(groups.router)
app.include_router(games.router)
app.include_router(me.router)


@app.get("/swagger", include_in_schema=False)
async def swagger_legacy_redirect() -> RedirectResponse:
    """Previous docs path; Swagger UI lives at `/docs`."""
    return RedirectResponse(url="/docs", status_code=307)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
