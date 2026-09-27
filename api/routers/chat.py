import asyncio
import json
import traceback
from typing import Any, AsyncGenerator, Dict, List, Optional

from ai_prompter import Prompter
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from loguru import logger
from pydantic import BaseModel, Field

from api.routers._chat_shared import (
    ChatMessage,
    SuccessResponse,
    extract_chat_messages,
    get_session_or_404,
    parse_follow_up_questions,
)
from open_notebook.ai.models import DefaultModels
from open_notebook.ai.provision import provision_langchain_model
from open_notebook.database.repository import ensure_record_id, repo_query
from open_notebook.domain.notebook import ChatSession, Notebook
from open_notebook.exceptions import (
    NotFoundError,
    OpenNotebookError,
)
from open_notebook.graphs.chat import graph as chat_graph
from open_notebook.utils import token_count
from open_notebook.utils.context_builder import build_notebook_context
from open_notebook.utils.graph_utils import get_session_message_count

router = APIRouter()


# Request/Response models
class CreateSessionRequest(BaseModel):
    notebook_id: str = Field(..., description="Notebook ID to create session for")
    title: Optional[str] = Field(None, description="Optional session title")
    model_override: Optional[str] = Field(
        None, description="Optional model override for this session"
    )


class UpdateSessionRequest(BaseModel):
    title: Optional[str] = Field(None, description="New session title")
    model_override: Optional[str] = Field(
        None, description="Model override for this session"
    )


class ChatSessionResponse(BaseModel):
    id: str = Field(..., description="Session ID")
    title: str = Field(..., description="Session title")
    notebook_id: Optional[str] = Field(None, description="Notebook ID")
    created: str = Field(..., description="Creation timestamp")
    updated: str = Field(..., description="Last update timestamp")
    message_count: Optional[int] = Field(
        None, description="Number of messages in session"
    )
    model_override: Optional[str] = Field(
        None, description="Model override for this session"
    )


class ChatSessionWithMessagesResponse(ChatSessionResponse):
    messages: List[ChatMessage] = Field(
        default_factory=list, description="Session messages"
    )


class ExecuteChatRequest(BaseModel):
    session_id: str = Field(..., description="Chat session ID")
    message: str = Field(..., description="User message content")
    context: Dict[str, Any] = Field(
        ..., description="Chat context with sources and notes"
    )
    model_override: Optional[str] = Field(
        None, description="Optional model override for this message"
    )


class ExecuteChatResponse(BaseModel):
    session_id: str = Field(..., description="Session ID")
    messages: List[ChatMessage] = Field(..., description="Updated message list")


class BuildContextRequest(BaseModel):
    notebook_id: str = Field(..., description="Notebook ID")
    context_config: Dict[str, Any] = Field(..., description="Context configuration")


class BuildContextResponse(BaseModel):
    context: Dict[str, Any] = Field(..., description="Built context data")
    token_count: int = Field(..., description="Estimated token count")
    char_count: int = Field(..., description="Character count")


@router.get("/chat/sessions", response_model=List[ChatSessionResponse])
async def get_sessions(notebook_id: str = Query(..., description="Notebook ID")):
    """Get all chat sessions for a notebook."""
    try:
        # Get notebook to verify it exists
        notebook = await Notebook.get(notebook_id)
        if not notebook:
            raise HTTPException(status_code=404, detail="Notebook not found")

        # Get sessions for this notebook
        sessions_list = await notebook.get_chat_sessions()

        results = []
        for session in sessions_list:
            session_id = str(session.id)

            # Get message count from LangGraph state
            msg_count = await get_session_message_count(chat_graph, session_id)

            results.append(
                ChatSessionResponse(
                    id=session.id or "",
                    title=session.title or "Untitled Session",
                    notebook_id=notebook_id,
                    created=str(session.created),
                    updated=str(session.updated),
                    message_count=msg_count,
                    model_override=getattr(session, "model_override", None),
                )
            )

        return results
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Notebook not found")
    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error fetching chat sessions: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Error fetching chat sessions: {str(e)}"
        )


@router.post("/chat/sessions", response_model=ChatSessionResponse)
async def create_session(request: CreateSessionRequest):
    """Create a new chat session."""
    try:
        # Verify notebook exists
        notebook = await Notebook.get(request.notebook_id)
        if not notebook:
            raise HTTPException(status_code=404, detail="Notebook not found")

        # Create new session
        session = ChatSession(
            title=request.title
            or f"Chat Session {asyncio.get_event_loop().time():.0f}",
            model_override=request.model_override,
        )
        await session.save()

        # Relate session to notebook
        await session.relate_to_notebook(request.notebook_id)

        return ChatSessionResponse(
            id=session.id or "",
            title=session.title or "",
            notebook_id=request.notebook_id,
            created=str(session.created),
            updated=str(session.updated),
            message_count=0,
            model_override=session.model_override,
        )
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Notebook not found")
    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error creating chat session: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Error creating chat session: {str(e)}"
        )


@router.get(
    "/chat/sessions/{session_id}", response_model=ChatSessionWithMessagesResponse
)
async def get_session(session_id: str):
    """Get a specific session with its messages."""
    try:
        # Get session (normalizes the ID and 404s if missing)
        full_session_id, session = await get_session_or_404(session_id)

        # Get session state from LangGraph to retrieve messages
        # Use sync get_state() in a thread since SqliteSaver doesn't support async
        thread_state = await asyncio.to_thread(
            chat_graph.get_state,
            config=RunnableConfig(configurable={"thread_id": full_session_id}),
        )

        # Extract messages from state
        messages: list[ChatMessage] = []
        if thread_state and thread_state.values and "messages" in thread_state.values:
            messages = extract_chat_messages(thread_state.values["messages"])

        # Find notebook_id (we need to query the relationship)
        notebook_query = await repo_query(
            "SELECT out FROM refers_to WHERE in = $session_id",
            {"session_id": ensure_record_id(full_session_id)},
        )

        notebook_id = notebook_query[0]["out"] if notebook_query else None

        if not notebook_id:
            # This might be an old session created before API migration
            logger.warning(
                f"No notebook relationship found for session {session_id} - may be an orphaned session"
            )

        return ChatSessionWithMessagesResponse(
            id=session.id or "",
            title=session.title or "Untitled Session",
            notebook_id=notebook_id,
            created=str(session.created),
            updated=str(session.updated),
            message_count=len(messages),
            messages=messages,
            model_override=getattr(session, "model_override", None),
        )
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Session not found")
    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error fetching session: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error fetching session: {str(e)}")


@router.put("/chat/sessions/{session_id}", response_model=ChatSessionResponse)
async def update_session(session_id: str, request: UpdateSessionRequest):
    """Update session title."""
    try:
        # Get session (normalizes the ID and 404s if missing)
        full_session_id, session = await get_session_or_404(session_id)

        update_data = request.model_dump(exclude_unset=True)

        if "title" in update_data:
            session.title = update_data["title"]

        if "model_override" in update_data:
            session.model_override = update_data["model_override"]

        await session.save()

        # Find notebook_id
        notebook_query = await repo_query(
            "SELECT out FROM refers_to WHERE in = $session_id",
            {"session_id": ensure_record_id(full_session_id)},
        )
        notebook_id = notebook_query[0]["out"] if notebook_query else None

        # Get message count from LangGraph state
        msg_count = await get_session_message_count(chat_graph, full_session_id)

        return ChatSessionResponse(
            id=session.id or "",
            title=session.title or "",
            notebook_id=notebook_id,
            created=str(session.created),
            updated=str(session.updated),
            message_count=msg_count,
            model_override=session.model_override,
        )
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Session not found")
    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error updating session: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error updating session: {str(e)}")


@router.delete("/chat/sessions/{session_id}", response_model=SuccessResponse)
async def delete_session(session_id: str):
    """Delete a chat session."""
    try:
        # Get session (normalizes the ID and 404s if missing)
        _full_session_id, session = await get_session_or_404(session_id)

        await session.delete()

        return SuccessResponse(success=True, message="Session deleted successfully")
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Session not found")
    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error deleting session: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error deleting session: {str(e)}")


@router.post("/chat/execute", response_model=ExecuteChatResponse)
async def execute_chat(request: ExecuteChatRequest):
    """Execute a chat request and get AI response."""
    try:
        # Verify session exists (normalizes the ID and 404s if missing)
        full_session_id, session = await get_session_or_404(request.session_id)

        # Fetch notebook linked to this session
        notebook_query = await repo_query(
            "SELECT out FROM refers_to WHERE in = $session_id",
            {"session_id": ensure_record_id(full_session_id)},
        )
        notebook = None
        if notebook_query:
            notebook = await Notebook.get(notebook_query[0]["out"])

        # Determine model override (per-request override takes precedence over session-level)
        model_override = (
            request.model_override
            if request.model_override is not None
            else getattr(session, "model_override", None)
        )

        # Get current state
        # Use sync get_state() in a thread since SqliteSaver doesn't support async
        current_state = await asyncio.to_thread(
            chat_graph.get_state,
            config=RunnableConfig(configurable={"thread_id": full_session_id}),
        )

        # Prepare state for execution
        state_values = current_state.values if current_state else {}
        state_values["messages"] = state_values.get("messages", [])
        state_values["context"] = request.context
        state_values["notebook"] = notebook
        state_values["model_override"] = model_override

        # Add user message to state
        from langchain_core.messages import HumanMessage

        user_message = HumanMessage(content=request.message)
        state_values["messages"].append(user_message)

        # Execute chat graph in a thread so the synchronous LangGraph invoke
        # (SqliteSaver checkpoints are sync) doesn't block the event loop and
        # freeze the rest of the API while the LLM responds. Mirrors the
        # get_state() calls above.
        # The lambda pins down which `invoke` overload is used; asyncio.to_thread
        # can't resolve overloaded callables on its own. The ignore is a langgraph
        # typing limitation: it accepts a partial state dict at runtime, but the
        # signature requires the full state type.
        result = await asyncio.to_thread(
            lambda: chat_graph.invoke(
                input=state_values,  # type: ignore[arg-type]
                config=RunnableConfig(
                    configurable={
                        "thread_id": full_session_id,
                        "model_id": model_override,
                    }
                ),
            )
        )

        # Update session timestamp
        await session.save()

        # Convert messages to response format
        messages = extract_chat_messages(result.get("messages", []))

        return ExecuteChatResponse(session_id=request.session_id, messages=messages)
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Session not found")
    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        # Log detailed error with context for debugging
        logger.error(
            f"Error executing chat: {str(e)}\n"
            f"  Session ID: {request.session_id}\n"
            f"  Model override: {request.model_override}\n"
            f"  Traceback:\n{traceback.format_exc()}"
        )
        raise HTTPException(status_code=500, detail=f"Error executing chat: {str(e)}")


async def stream_chat_response(
    session_id: str,
    message: str,
    context: Dict[str, Any],
    model_override: Optional[str] = None,
) -> AsyncGenerator[str, None]:
    """Stream notebook chat response token-by-token with thinking via SSE."""
    try:
        # Verify session exists (normalizes the ID and 404s if missing)
        full_session_id, session = await get_session_or_404(session_id)

        # Fetch notebook linked to this session
        notebook_query = await repo_query(
            "SELECT out FROM refers_to WHERE in = $session_id",
            {"session_id": ensure_record_id(full_session_id)},
        )
        notebook = None
        if notebook_query:
            notebook = await Notebook.get(notebook_query[0]["out"])

        # Determine model override
        effective_model_override = (
            model_override
            if model_override is not None
            else getattr(session, "model_override", None)
        )

        # Get current state
        current_state = await asyncio.to_thread(
            chat_graph.get_state,
            config=RunnableConfig(configurable={"thread_id": full_session_id}),
        )

        state_values = current_state.values if current_state else {}
        state_values["messages"] = list(state_values.get("messages", []))
        state_values["context"] = context
        state_values["notebook"] = notebook
        state_values["model_override"] = effective_model_override

        user_message = HumanMessage(content=message)
        state_values["messages"].append(user_message)

        # 1. Yield user message event
        user_event = {"type": "user_message", "content": message, "timestamp": None}
        yield f"data: {json.dumps(user_event)}\n\n"

        # 2. Render prompt
        system_prompt = Prompter(prompt_template="chat/system").render(
            data=state_values
        )
        payload = [SystemMessage(content=system_prompt)] + state_values["messages"]

        # 3. Provision streaming model
        defaults = await DefaultModels.get_instance()
        chat_max_tokens = getattr(defaults, "chat_max_tokens", None) or 8192

        model = await provision_langchain_model(
            str(payload),
            effective_model_override,
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

            # Persist messages to LangGraph checkpoint in a worker thread
            await asyncio.to_thread(
                chat_graph.update_state,
                config=RunnableConfig(configurable={"thread_id": full_session_id}),
                values={
                    "messages": [user_message, ai_message],
                    "context": context,
                    "notebook": notebook,
                    "model_override": effective_model_override,
                },
            )

            await session.save()
            stream_completed = True

            # Send completion event with parsed follow_ups if any
            follow_ups, _ = parse_follow_up_questions(full_content)
            completion_event = {"type": "complete", "follow_ups": follow_ups}
            yield f"data: {json.dumps(completion_event)}\n\n"

        except (asyncio.CancelledError, GeneratorExit):
            logger.warning(f"Notebook chat stream cancelled or disconnected for session {full_session_id}")
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
                        chat_graph.update_state,
                        config=RunnableConfig(configurable={"thread_id": full_session_id}),
                        values={
                            "messages": [user_message, partial_ai],
                            "context": context,
                            "notebook": notebook,
                            "model_override": effective_model_override,
                        },
                    )
                    await session.save()
                    logger.info(f"Persisted partial notebook chat message ({len(partial_content)} chars) for session {full_session_id}")
                except Exception as save_err:
                    logger.error(f"Error persisting partial notebook chat stream: {save_err}")

    except Exception as e:
        from open_notebook.utils.error_classifier import classify_error

        _, error_message = classify_error(e)
        logger.error(f"Error in chat streaming: {str(e)}\n{traceback.format_exc()}")
        error_event = {"type": "error", "message": error_message}
        yield f"data: {json.dumps(error_event)}\n\n"


@router.post("/chat/stream")
async def stream_chat(request: ExecuteChatRequest):
    """Stream notebook chat response token-by-token with thinking via Server-Sent Events (SSE)."""
    return StreamingResponse(
        stream_chat_response(
            session_id=request.session_id,
            message=request.message,
            context=request.context,
            model_override=request.model_override,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/chat/context", response_model=BuildContextResponse)
async def build_context(request: BuildContextRequest):
    """Build context for a notebook based on context configuration."""
    try:
        # Verify notebook exists
        notebook = await Notebook.get(request.notebook_id)
        if not notebook:
            raise HTTPException(status_code=404, detail="Notebook not found")

        context_data, total_content = await build_notebook_context(
            notebook, request.context_config
        )

        char_count = len(total_content)
        estimated_tokens = token_count(total_content) if total_content else 0

        return BuildContextResponse(
            context=context_data, token_count=estimated_tokens, char_count=char_count
        )
    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error building context: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error building context: {str(e)}")
