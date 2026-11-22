from fastapi import APIRouter

from app.api.routes import (
    agent,
    customers,
    escalations,
    health,
    insights,
    memories,
    operator,
    orders,
    studio,
    tickets,
    ui,
    workspace,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(customers.router)
api_router.include_router(orders.router)
api_router.include_router(tickets.router)
api_router.include_router(escalations.router)
api_router.include_router(agent.router)
api_router.include_router(memories.router)
api_router.include_router(ui.router)
api_router.include_router(operator.router)
api_router.include_router(studio.router)
api_router.include_router(workspace.router)
api_router.include_router(insights.router)
