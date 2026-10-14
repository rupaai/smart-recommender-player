"""Domain routers assembled by the FastAPI composition root."""


def build_routers(handlers):
    """Build every domain router with the app's live dependency mapping."""
    from . import (
        catalogue,
        downloads,
        jellyfin,
        pages,
        playback,
        platform,
        profiles,
        recommendations,
    )

    return [
        platform.build_router(handlers),
        jellyfin.build_router(handlers),
        recommendations.build_router(handlers),
        profiles.build_router(handlers),
        playback.build_router(handlers),
        catalogue.build_router(handlers),
        downloads.build_router(handlers),
        pages.build_router(handlers),
    ]
