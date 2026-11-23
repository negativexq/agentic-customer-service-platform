import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.agent.runtime import AgentRuntime
from app.agent.schemas import AgentChatRequest, AgentResponse
from app.api.dependencies import get_execution_context
from app.api.errors import raise_http_for_tool_error
from app.core.context import ExecutionContext
from app.core.database import get_db
from app.resilience.errors import RateLimitExceededError
from app.services.conversation_messages import record_chat_turn
from app.tools.base import ToolError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agent", tags=["agent"])


def get_agent_runtime(request: Request) -> AgentRuntime:
    runtime: AgentRuntime = request.app.state.agent_runtime
    return runtime


@router.post(
    "/chat",
    response_model=AgentResponse,
    response_model_exclude={
        "pending_action": {
            "action_id",
            "conversation_id",
            "tenant_id",
            "actor_id",
            "actor_type",
            "effective_customer_id",
            "tool_name",
            "arguments",
            "risk_level",
            "intent",
            "collected_entities",
            "validation_context",
            "policy_inputs",
            "policy_inputs_hash",
            "created_at",
        },
        "tool_call": {"name", "result"},
        "citations": True,
        "proposal": True,
        "provider_metadata": True,
    },
)
def chat(
    request: AgentChatRequest,
    execution_context: ExecutionContext = Depends(get_execution_context),
    session: Session = Depends(get_db),
    runtime: AgentRuntime = Depends(get_agent_runtime),
) -> AgentResponse:
    try:
        if request.execution_mode.value == "recorded_replay":
            response = runtime.run(
                context=execution_context,
                message=request.message,
                session=session,
            )
        else:
            response = runtime.run(
                context=execution_context,
                message=request.message,
                session=session,
                execution_mode=request.execution_mode,
            )
    except RateLimitExceededError as error:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Request rate limit exceeded.",
            headers={"Retry-After": str(max(1, int(error.retry_after_seconds)))},
        ) from None
    except ToolError as error:
        raise_http_for_tool_error(error)
    _record_transcript(session, execution_context, request.message, response)
    return response


def _record_transcript(
    session: Session, context: ExecutionContext, message: str, response: AgentResponse
) -> None:
    """The reply is already decided; a transcript failure must not fail the customer turn."""

    try:
        record_chat_turn(session, context, message, response)
    except Exception as error:  # noqa: BLE001 - transcript is display data
        session.rollback()
        logger.warning(
            "Conversation transcript write failed.",
            extra={"transcript_error_type": type(error).__name__},
        )
