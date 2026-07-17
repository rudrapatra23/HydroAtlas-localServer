from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from core.config import Settings, get_settings

router = APIRouter(tags=["health"])


@router.get("/health")
async def health(
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, str]:
    """A simple check to see if the server is up and running."""
    return {"status": "healthy", "version": settings.version}
