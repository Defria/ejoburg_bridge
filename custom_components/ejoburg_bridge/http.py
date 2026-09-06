"""Authenticated HTTP views for cached e-Joburg documents."""

from __future__ import annotations

import os

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .const import DOCUMENT_URL, DOMAIN
from .coordinator import EJoburgCoordinator


class EJoburgDocumentView(HomeAssistantView):
    """Serve a document registered by an active config entry."""

    url = DOCUMENT_URL
    name = "api:ejoburg_bridge:document"
    requires_auth = True

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    async def get(
        self, request: web.Request, entry_id: str, document_id: str
    ) -> web.StreamResponse:
        coordinator = self._hass.data.get(DOMAIN, {}).get(entry_id)
        if not isinstance(coordinator, EJoburgCoordinator):
            raise web.HTTPNotFound()

        document = coordinator.get_document(document_id)
        if document is None:
            raise web.HTTPNotFound()

        path, content_type, download_name = document
        if not await self._hass.async_add_executor_job(os.path.isfile, path):
            raise web.HTTPNotFound()

        response = web.FileResponse(path)
        response.content_type = content_type
        response.headers["Content-Disposition"] = (
            f'inline; filename="{download_name.replace(chr(34), "")}"'
        )
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response
