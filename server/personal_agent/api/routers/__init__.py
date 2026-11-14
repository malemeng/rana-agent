"""REST routers included by :func:`personal_agent.api.include_rest_routes`."""

from . import (
    activities,
    approvals,
    auth,
    connections,
    conversations,
    goals,
    memories,
    schedules,
    settings,
    tasks,
)

rest_routers = (
    auth.router,
    conversations.router,
    goals.router,
    tasks.router,
    memories.router,
    schedules.router,
    approvals.router,
    settings.router,
    activities.router,
    connections.router,
)

__all__ = ["rest_routers"]
