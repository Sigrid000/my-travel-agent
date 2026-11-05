from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import __version__
from app.agent.orchestrator import TravelOrchestrator
from app.api.schemas import ChatRequest, ChatResponse
from app.config import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.orchestrator = TravelOrchestrator(settings=settings)
    yield


app = FastAPI(
    title="my-travel-agent",
    version=__version__,
    description="Leisure travel planning agent with tools, HITL, and JSONL traces.",
    lifespan=lifespan,
)


@app.get("/")
async def root():
    return {
        "service": "my-travel-agent",
        "version": __version__,
        "docs": "/docs",
        "chat": "POST /api/v1/chat",
    }


@app.get("/health")
async def health():
    settings = get_settings()
    return {
        "status": "ok",
        "has_openai_key": bool(settings.openai_api_key.strip()),
        "model": settings.openai_model,
    }


@app.post("/api/v1/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    orch: TravelOrchestrator = app.state.orchestrator
    result = await orch.handle(req.message, session_id=req.session_id)
    return ChatResponse(
        session_id=result.session_id,
        message=result.message,
        intent=result.intent,
        lane=result.lane,
        blocked=result.blocked,
        trace=result.trace,
        metadata={k: v for k, v in result.metadata.items() if k != "observations"},
    )
