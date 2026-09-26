"""Commercial backend: paid hosted transcript API.

Intentionally a stub. The interface is fixed so that swapping ``local`` for
``hosted`` in .env is the only change needed once a provider is chosen.
"""
from __future__ import annotations

from . import Transcript


class HostedBackend:
    name = "hosted"

    def __init__(self, api_key: str | None = None, endpoint: str | None = None):
        self.api_key, self.endpoint = api_key, endpoint

    def fetch(self, video_id: str) -> Transcript:
        raise NotImplementedError(
            "TRANSCRIPT_BACKEND=hosted is not implemented yet. "
            "Use TRANSCRIPT_BACKEND=local (verification, residential IP only) or null."
        )
