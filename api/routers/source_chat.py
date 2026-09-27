import asyncio
import json
from typing import AsyncGenerator, List, Optional

from fastapi import APIRouter, HTTPException, Path
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from loguru import logger
from pydantic import BaseModel, Field

from ai_prompter import Prompter
from api.routers._chat_shared import (
    ChatMessage,
    SuccessResponse,
    extract_chat_messages,
    get_source_or_404,
    get_verified_source_session,
    parse_follow_up_questions,
)
from open_notebook.ai.models import DefaultModels
from open_notebook.ai.provision import provision_langchain_model
from open_notebook.database.repository import ensure_record_id, repo_query
from open_notebook.domain.notebook import ChatSession, Source, SourceInsight
from open_notebook.exceptions import (
    NotFoundError,
    OpenNotebookError,
)
from open_notebook.graphs.source_chat import (
    _source_content_is_available,
    source_chat_graph as source_chat_graph,
)
from open_notebook.utils.context_builder import build_source_context, format_source_context
from open_notebook.utils.graph_utils import get_session_message_count

router = APIRouter()


# Request/Response models
class CreateSourceChatSessionRequest(BaseModel):
    source_id: str = Field(..., description="Source ID to create chat session for")
    title: Optional[str] = Field(None, description="Optional session title")
    model_override: Optional[str] = Field(
        None, description="Optional model override for this session"
    )

class UpdateSourceChatSessionRequest(BaseModel):
    title: Optional[str] = Field(None, description="New session title")
    model_override: Optional[str] = Field(
        None, description="Model override for this session"
    )

class ContextIndicator(BaseModel):
    sources: List[str] = Field(
        default_factory=list, description="Source IDs used in context"
    )
    insights: List[str] = Field(
        default_factory=list, description="Insight IDs used in context"
    )
    notes: List[str] = Field(
        default_factory=list, description="Note IDs used in context"
    )

class SourceChatSessionResponse(BaseModel):
    id: str = Field(..., description="Session ID")
    title: str = Field(..., description="Session title")
    source_id: str = Field(..., description="Source ID")
    model_override: Optional[str] = Field(
        None, description="Model override for this session"
    )
    created: str = Field(..., description="Creation timestamp")
    updated: str = Field(..., description="Last update timestamp")
    message_count: Optional[int] = Field(
        None, description="Number of messages in session"
    )

class SourceChatSessionWithMessagesResponse(SourceChatSessionResponse):
    messages: List[ChatMessage] = Field(
        default_factory=list, description="Session messages"
    )
    context_indicators: Optional[ContextIndicator] = Field(
        None, description="Context indicators from last response"
    )

class SendMessageRequest(BaseModel):
    message: str = Field(..., description="User message content")
    model_override: Optional[str] = Field(
        None, description="Optional model override for this message"
    )

@router.post(
    "/sources/{source_id}/chat/sessions", response_model=SourceChatSessionResponse
)
async def create_source_chat_session(
    request: CreateSourceChatSessionRequest,
    source_id: str = Path(..., description="Source ID"),
):
    """Create a new chat session for a source."""
    try:
        # Verify source exists (normalizes the ID and 404s if missing)
        full_source_id, _source = await get_source_or_404(source_id)

        # Create new session with model_override support
        session = ChatSession(
            title=request.title or f"Source Chat {asyncio.get_event_loop().time():.0f}",
            model_override=request.model_override,
        )
        await session.save()

        # Relate session to source using "refers_to" relation
        await session.relate("refers_to", full_source_id)

        return SourceChatSessionResponse(
            id=session.id or "",
            title=session.title or "Untitled Session",
            source_id=source_id,
            model_override=session.model_override,
            created=str(session.created),
            updated=str(session.updated),
            message_count=0,
        )
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Source not found")
    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error creating source chat session: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Error creating source chat session: {str(e)}"
        )


@router.get(
    "/sources/{source_id}/chat/sessions", response_model=List[SourceChatSessionResponse]
)
async def get_source_chat_sessions(source_id: str = Path(..., description="Source ID")):
    """Get all chat sessions for a source."""
    try:
        # Verify source exists (normalizes the ID and 404s if missing)
        full_source_id, _source = await get_source_or_404(source_id)

        # Get sessions that refer to this source - first get relations, then sessions
        relations = await repo_query(
            "SELECT in FROM refers_to WHERE out = $source_id",
            {"source_id": ensure_record_id(full_source_id)},
        )

        sessions = []
        for relation in relations:
            session_id_raw = relation.get("in")
            if session_id_raw:
                session_id = str(session_id_raw)

                session_result = await repo_query(
                    "SELECT * FROM $id", {"id": ensure_record_id(session_id)}
                )
                if session_result and len(session_result) > 0:
                    session_data = session_result[0]

                    # Get message count from LangGraph state
                    msg_count = await get_session_message_count(
                        source_chat_graph, session_id
                    )

                    sessions.append(
                        SourceChatSessionResponse(
                            id=session_data.get("id") or "",
                            title=session_data.get("title") or "Untitled Session",
                            source_id=source_id,
                            model_override=session_data.get("model_override"),
                            created=str(session_data.get("created")),
                            updated=str(session_data.get("updated")),
                            message_count=msg_count,
                        )
                    )

        # Sort sessions by created date (newest first)
        sessions.sort(key=lambda x: x.created, reverse=True)
        return sessions
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Source not found")
    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error fetching source chat sessions: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Error fetching source chat sessions: {str(e)}"
        )


@router.get(
    "/sources/{source_id}/chat/sessions/{session_id}",
    response_model=SourceChatSessionWithMessagesResponse,
)
async def get_source_chat_session(
    source_id: str = Path(..., description="Source ID"),
    session_id: str = Path(..., description="Session ID"),
):
    """Get a specific source chat session with its messages."""
    try:
        # Verify source + session exist and are related (404s otherwise)
        _full_source_id, _source, full_session_id, session = (
            await get_verified_source_session(source_id, session_id)
        )

        # Get session state from LangGraph to retrieve messages
        # Use sync get_state() in a thread since SqliteSaver doesn't support async
        thread_state = await asyncio.to_thread(
            source_chat_graph.get_state,
            config=RunnableConfig(configurable={"thread_id": full_session_id}),
        )

        # Extract messages from state
        messages: list[ChatMessage] = []
        context_indicators = None

        if thread_state and thread_state.values:
            # Extract messages
            if "messages" in thread_state.values:
                messages = extract_chat_messages(thread_state.values["messages"])

            # Extract context indicators from the last state
            if "context_indicators" in thread_state.values:
                context_data = thread_state.values["context_indicators"]
                context_indicators = ContextIndicator(
                    sources=context_data.get("sources", []),
                    insights=context_data.get("insights", []),
                    notes=context_data.get("notes", []),
                )

        return SourceChatSessionWithMessagesResponse(
            id=session.id or "",
            title=session.title or "Untitled Session",
            source_id=source_id,
            model_override=getattr(session, "model_override", None),
            created=str(session.created),
            updated=str(session.updated),
            message_count=len(messages),
            messages=messages,
            context_indicators=context_indicators,
        )
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Source or session not found")
    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error fetching source chat session: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Error fetching source chat session: {str(e)}"
        )


@router.put(
    "/sources/{source_id}/chat/sessions/{session_id}",
    response_model=SourceChatSessionResponse,
)
async def update_source_chat_session(
    request: UpdateSourceChatSessionRequest,
    source_id: str = Path(..., description="Source ID"),
    session_id: str = Path(..., description="Session ID"),
):
    """Update source chat session title and/or model override."""
    try:
        # Verify source + session exist and are related (404s otherwise)
        _full_source_id, _source, full_session_id, session = (
            await get_verified_source_session(source_id, session_id)
        )

        # Update session fields
        if request.title is not None:
            session.title = request.title
        if request.model_override is not None:
            session.model_override = request.model_override

        await session.save()

        # Get message count from LangGraph state
        msg_count = await get_session_message_count(source_chat_graph, full_session_id)

        return SourceChatSessionResponse(
            id=session.id or "",
            title=session.title or "Untitled Session",
            source_id=source_id,
            model_override=getattr(session, "model_override", None),
            created=str(session.created),
            updated=str(session.updated),
            message_count=msg_count,
        )
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Source or session not found")
    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error updating source chat session: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Error updating source chat session: {str(e)}"
        )


@router.delete(
    "/sources/{source_id}/chat/sessions/{session_id}", response_model=SuccessResponse
)
async def delete_source_chat_session(
    source_id: str = Path(..., description="Source ID"),
    session_id: str = Path(..., description="Session ID"),
):
    """Delete a source chat session."""
    try:
        # Verify source + session exist and are related (404s otherwise)
        _full_source_id, _source, full_session_id, session = (
            await get_verified_source_session(source_id, session_id)
        )

        await session.delete()

        return SuccessResponse(
            success=True, message="Source chat session deleted successfully"
        )
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Source or session not found")
    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error deleting source chat session: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Error deleting source chat session: {str(e)}"
        )


async def stream_source_chat_response(
    session_id: str, source_id: str, message: str, model_override: Optional[str] = None
) -> AsyncGenerator[str, None]:
    """Stream the source chat response as Server-Sent Events."""
    try:
        # Get current state
        # Use sync get_state() in a thread since SqliteSaver doesn't support async
        current_state = await asyncio.to_thread(
            source_chat_graph.get_state,
            config=RunnableConfig(configurable={"thread_id": session_id}),
        )

        # Prepare state for execution
        state_values = current_state.values if current_state else {}
        state_values["messages"] = state_values.get("messages", [])
        state_values["source_id"] = source_id
        state_values["model_override"] = model_override

        # Add user message to state
        user_message = HumanMessage(content=message)
        state_values["messages"].append(user_message)

        # Send user message event
        user_event = {"type": "user_message", "content": message, "timestamp": None}
        yield f"data: {json.dumps(user_event)}\n\n"

        # 2. Build context
        context_data = await build_source_context(
            source_id=source_id,
            max_tokens=50000,
        )

        source = None
        insights = []
        context_indicators: dict[str, list[str | None]] = {
            "sources": [],
            "insights": [],
            "notes": [],
        }

        if context_data.get("sources"):
            source_info = context_data["sources"][0]
            source = Source(**source_info) if isinstance(source_info, dict) else source_info
            if (
                isinstance(source_info, dict)
                and _source_content_is_available(source_info, context_data)
                and source.id
            ):
                context_indicators["sources"].append(source.id)

        if context_data.get("insights"):
            for insight_data in context_data["insights"]:
                insight = (
                    SourceInsight(**insight_data)
                    if isinstance(insight_data, dict)
                    else insight_data
                )
                insights.append(insight)
                context_indicators["insights"].append(insight.id)

        formatted_context = format_source_context(context_data)

        prompt_data = {
            "source": source.model_dump() if source else None,
            "insights": [insight.model_dump() for insight in insights] if insights else [],
            "context": formatted_context,
            "context_indicators": context_indicators,
        }

        system_prompt = Prompter(prompt_template="source_chat/system").render(
            data=prompt_data
        )
        payload = [SystemMessage(content=system_prompt)] + state_values["messages"]

        # 3. Provision streaming model
        defaults = await DefaultModels.get_instance()
        chat_max_tokens = getattr(defaults, "chat_max_tokens", None) or 8192

        model = await provision_langchain_model(
            str(payload),
            model_override,
            "chat",
            streaming=True,
            max_tokens=chat_max_tokens,
        )

        full_content_parts = []
        full_thinking_parts = []
        stream_completed = False

        try:
            async for chunk in model.astream(payload):
                thinking_delta = ""
                if hasattr(chunk, "additional_kwargs") and isinstance(
                    chunk.additional_kwargs, dict
                ):
                    thinking_delta = (
                        chunk.additional_kwargs.get("thinking")
                        or chunk.additional_kwargs.get("reasoning_content")
                        or ""
                    )

                content_delta = chunk.content if hasattr(chunk, "content") else str(chunk)

                if thinking_delta:
                    full_thinking_parts.append(thinking_delta)
                if content_delta:
                    full_content_parts.append(content_delta)

                if thinking_delta or content_delta:
                    current_thinking = (
                        "".join(full_thinking_parts) if full_thinking_parts else None
                    )
                    ai_event = {
                        "type": "ai_message",
                        "content": content_delta,
                        "thinking": current_thinking,
                        "thinking_delta": thinking_delta,
                        "timestamp": None,
                    }
                    yield f"data: {json.dumps(ai_event)}\n\n"

            # 4. Finalize and persist
            full_content = "".join(full_content_parts)
            full_thinking = "".join(full_thinking_parts) if full_thinking_parts else None

            extra_kwargs = {}
            if full_thinking:
                extra_kwargs["thinking"] = full_thinking
                extra_kwargs["reasoning_content"] = full_thinking

            ai_message = AIMessage(content=full_content, additional_kwargs=extra_kwargs)

            await asyncio.to_thread(
                source_chat_graph.update_state,
                config=RunnableConfig(configurable={"thread_id": session_id}),
                values={
                    "messages": [user_message, ai_message],
                    "source": source,
                    "insights": insights,
                    "context": formatted_context,
                    "context_indicators": context_indicators,
                    "model_override": model_override,
                },
            )

            stream_completed = True

            # Stream context indicators
            if context_indicators.get("sources") or context_indicators.get("insights"):
                context_event = {
                    "type": "context_indicators",
                    "data": context_indicators,
                }
                yield f"data: {json.dumps(context_event)}\n\n"

            # Send completion signal with parsed follow_ups if any
            follow_ups, _ = parse_follow_up_questions(full_content)
            completion_event = {"type": "complete", "follow_ups": follow_ups}
            yield f"data: {json.dumps(completion_event)}\n\n"

        except (asyncio.CancelledError, GeneratorExit):
            logger.warning(f"Source chat stream cancelled or disconnected for session {session_id}")
            raise
        finally:
            if not stream_completed and (full_content_parts or full_thinking_parts):
                try:
                    partial_content = "".join(full_content_parts)
                    partial_thinking = "".join(full_thinking_parts) if full_thinking_parts else None
                    extra_kwargs = {}
                    if partial_thinking:
                        extra_kwargs["thinking"] = partial_thinking
                        extra_kwargs["reasoning_content"] = partial_thinking
                    partial_ai = AIMessage(content=partial_content, additional_kwargs=extra_kwargs)
                    await asyncio.to_thread(
                        source_chat_graph.update_state,
                        config=RunnableConfig(configurable={"thread_id": session_id}),
                        values={
                            "messages": [user_message, partial_ai],
                            "source": source,
                            "insights": insights,
                            "context": formatted_context,
                            "context_indicators": context_indicators,
                            "model_override": model_override,
                        },
                    )
                    logger.info(f"Persisted partial source chat message ({len(partial_content)} chars) for session {session_id}")
                except Exception as save_err:
                    logger.error(f"Error persisting partial source chat stream: {save_err}")

    except Exception as e:
        from open_notebook.utils.error_classifier import classify_error

        _, error_message = classify_error(e)
        logger.error(f"Error in source chat streaming: {str(e)}")
        error_event = {"type": "error", "message": error_message}
        yield f"data: {json.dumps(error_event)}\n\n"


@router.post("/sources/{source_id}/chat/sessions/{session_id}/messages")
async def send_message_to_source_chat(
    request: SendMessageRequest,
    source_id: str = Path(..., description="Source ID"),
    session_id: str = Path(..., description="Session ID"),
):
    """Send a message to source chat session with SSE streaming response."""
    try:
        # Verify source + session exist and are related (404s otherwise)
        full_source_id, _source, full_session_id, session = (
            await get_verified_source_session(source_id, session_id)
        )

        if not request.message:
            raise HTTPException(status_code=400, detail="Message content is required")

        # Determine model override (request override takes precedence over session override)
        model_override = request.model_override or getattr(
            session, "model_override", None
        )

        # Update session timestamp
        await session.save()

        # Return streaming response
        return StreamingResponse(
            stream_source_chat_response(
                session_id=full_session_id,
                source_id=full_source_id,
                message=request.message,
                model_override=model_override,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error sending message to source chat: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error sending message: {str(e)}")
