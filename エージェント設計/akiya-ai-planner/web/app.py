"""
web/app.py
==========
「空き家AIプランナー」のチャットUIを提供するFastAPIアプリ。

起動方法:
    export GEMINI_API_KEY=xxxxx   # または プロジェクトルートの .env に記載
    uvicorn web.app:app --reload --port 8000

    ブラウザで http://localhost:8000 を開く(「チャットで調べる/地図で調べる」の選択画面)。
    チャット画面は http://localhost:8000/chat 。

設計メモ(B・C担当向け):
- セッションはインメモリの dict で管理しているだけのプロトタイプ実装です。
  複数人での同時利用やプロセス再起動をまたぐ永続化が必要になったら、
  SESSIONS を Redis 等に差し替えてください。
- POST /api/chat のレスポンスに含まれる tool_calls には、そのターンで実行された
  search_akiya / estimate_renovation_cost / simulate_income / search_subsidies の
  生の結果(候補一覧・座標・コスト・収支・補助金など)がそのまま入っています。
  C担当の地図(Leaflet.js)・グラフ(Chart.js)表示は、この tool_calls を
  そのまま入力として使う想定です。
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# プロジェクトルート(agent.py, tools.py がある場所)をimportパスに追加
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from agent import AkiyaAgent  # noqa: E402

app = FastAPI(title="空き家AIプランナー")

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# session_id -> AkiyaAgent (プロトタイプにつきインメモリ。プロセス再起動で消える)
SESSIONS: dict[str, AkiyaAgent] = {}


class ChatRequest(BaseModel):
    session_id: str | None = None
    message: str


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    tool_calls: list[dict]


def _get_or_create_agent(session_id: str | None) -> tuple[str, AkiyaAgent]:
    if session_id and session_id in SESSIONS:
        return session_id, SESSIONS[session_id]
    new_id = session_id or str(uuid.uuid4())
    try:
        SESSIONS[new_id] = AkiyaAgent()
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    return new_id, SESSIONS[new_id]


@app.get("/")
def landing():
    return FileResponse(STATIC_DIR / "landing.html")


@app.get("/chat")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health():
    return {"ok": True}


@app.post("/api/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    if not req.message or not req.message.strip():
        raise HTTPException(status_code=400, detail="message は必須です。")

    session_id, agent = _get_or_create_agent(req.session_id)
    try:
        result = agent.send(req.message)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"エージェントの応答生成に失敗しました: {e}") from e

    tool_calls = [
        {"name": tc.name, "args": tc.args, "result": tc.result}
        for tc in result.tool_calls
    ]
    return ChatResponse(session_id=session_id, reply=result.reply, tool_calls=tool_calls)


@app.post("/api/reset")
def reset(session_id: str):
    SESSIONS.pop(session_id, None)
    return {"ok": True}
