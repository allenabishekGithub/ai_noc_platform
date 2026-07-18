from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

from .db import get_db_status, init_db, list_table_rows
from .models import ChatRequest, CreateAgentRequest, CreateSpecRequest, RouteRequest
from .repository import ensure_default_knowledge, get_session_state, list_knowledge_items
from .service import MultiSmartService


app = FastAPI(title="Multi Smart Agents", version="0.1.0")
service = MultiSmartService()
UI_PATH = Path(__file__).resolve().parent / "ui.html"


@app.on_event("startup")
def _startup() -> None:
    init_db()
    ensure_default_knowledge()


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return UI_PATH.read_text(encoding="utf-8")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/specs")
def api_list_specs():
    return service.list_specs()


@app.post("/api/specs/generate")
def api_generate_spec(req: CreateSpecRequest):
    return service.generate_spec(req.prompt)


@app.post("/api/specs")
def api_create_spec(req: CreateSpecRequest):
    return service.create_spec(req.prompt)


@app.get("/api/agents")
def api_list_agents():
    return service.list_instances()


@app.post("/api/agents")
def api_create_agent(req: CreateAgentRequest):
    try:
        return service.instantiate(
            spec_id=req.spec_id,
            create_from_prompt=req.create_from_prompt,
            display_name=req.display_name,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/agents/{instance_id}/chat")
def api_chat(instance_id: str, req: ChatRequest):
    try:
        return service.chat(instance_id, req.message)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/route")
def api_route(req: RouteRequest):
    try:
        return service.route(req.message, auto_create=req.auto_create)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/submit")
def api_submit(req: RouteRequest):
    try:
        return service.submit(req.message, auto_create=req.auto_create)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/executions/{execution_id}")
def api_execution(execution_id: str):
    try:
        return service.get_execution(execution_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/admin/sqlite/status")
def api_sqlite_status():
    return get_db_status()


@app.get("/api/admin/sqlite/table/{table_name}")
def api_sqlite_table(table_name: str, limit: int = 100):
    try:
        return {
            "table": table_name,
            "rows": list_table_rows(table_name, limit=limit),
        }
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/admin/mcp/status")
def api_mcp_status():
    return {
        "configured_servers": service.mcp_client.server_urls,
        "discovered_tools": service.mcp_client.capability_names(),
        "available": service.mcp_client.has_mcp(),
    }


@app.get("/api/admin/session/{session_id}")
def api_session_state(session_id: str):
    return get_session_state(session_id)


@app.get("/api/admin/knowledge")
def api_knowledge(category: str | None = None):
    return list_knowledge_items(category)
