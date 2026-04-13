from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import JSONResponse

from backend.api.deps import get_current_user
from backend.models.user import User

router = APIRouter(include_in_schema=False)


@router.get("/openapi.json", name="protected_openapi_json")
async def protected_openapi_json(
    request: Request,
    current_user: User = Depends(get_current_user),
) -> JSONResponse:
    return JSONResponse(request.app.openapi())


@router.get("/docs", name="protected_swagger_ui")
async def protected_swagger_ui(
    request: Request,
    current_user: User = Depends(get_current_user),
):
    return get_swagger_ui_html(
        openapi_url=str(request.url_for("protected_openapi_json")),
        title=f"{request.app.title} - Swagger UI",
    )


@router.get("/redoc", name="protected_redoc")
async def protected_redoc(
    request: Request,
    current_user: User = Depends(get_current_user),
):
    return get_redoc_html(
        openapi_url=str(request.url_for("protected_openapi_json")),
        title=f"{request.app.title} - ReDoc",
    )