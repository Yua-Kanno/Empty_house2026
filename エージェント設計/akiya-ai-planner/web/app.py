"""
web/app.py
==========
「空き家AIプランナー」のチャットUIを提供するFastAPIアプリ。

起動方法:
    export GEMINI_API_KEY=xxxxx   # または プロジェクトルートの .env に記載
    uvicorn web.app:app --reload --port 8000

    ブラウザで http://localhost:8000 を開く(「チャットで調べる/地図で調べる」の選択画面)。
    診断フォームは http://localhost:8000/diagnosis 、チャット画面は http://localhost:8000/chat 。

設計メモ(B・C担当向け):
- セッションはインメモリの dict で管理しているだけのプロトタイプ実装です。
  複数人での同時利用やプロセス再起動をまたぐ永続化が必要になったら、
  SESSIONS を Redis 等に差し替えてください。
- POST /api/chat のレスポンスに含まれる tool_calls には、そのターンで実行された
  search_akiya / estimate_renovation_cost / simulate_income / search_subsidies /
  generate_shop_image の生の結果(候補一覧・座標・コスト・収支・補助金・画像など)が
  そのまま入っています。C担当の地図(Leaflet.js)・グラフ(Chart.js)表示は、この
  tool_calls をそのまま入力として使う想定です。
- POST /api/quick-match は、診断フォーム(/diagnosis)の回答からGeminiとの会話を挟まずに
  直接 search_akiya を呼び出す「1段目のマッチング」です。エリア指定があり、かつ直接
  マッチする物件が見つかった場合は matched=true を返し、diagnosis.html側はそのままフロント
  (マップ・PDF出力画面)へ物件IDつきで遷移します。マッチしなかった場合は matched=false を
  返し、diagnosis.html側は /chat?diagnosis=... へフォールバックしてGeminiとの会話に入ります。
"""

from __future__ import annotations

import re
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

import tools  # noqa: E402
from agent import AkiyaAgent, DailyQuotaExceededError  # noqa: E402

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


class QuickMatchRequest(BaseModel):
    """診断フォーム(/diagnosis)の回答。1段目の直接マッチング判定に使う。"""

    job: str | None = None
    personality: list[str] | None = None
    environment: list[str] | None = None
    hobby: str | None = None
    lifestyle: str | None = None
    dream: str | None = None
    area: str | None = None
    budget: str | None = None
    want_subsidy: bool = False


class QuickMatchResponse(BaseModel):
    matched: bool
    property_id: int | None = None
    count: int
    results: list[dict]
    subsidies: list[dict] = []


_BUDGET_RE = re.compile(r"(\d+(?:\.\d+)?)\s*万")


def _parse_budget_man_yen(text: str | None) -> float | None:
    """「300万円くらいまで」のような自由入力から予算上限(万円)を抜き出す。取れなければNone。"""
    if not text:
        return None
    m = _BUDGET_RE.search(text)
    return float(m.group(1)) if m else None


def _match_reasons(p: dict, area: str, max_budget: float | None, use_type: str | None) -> list[str]:
    """この物件がなぜマッチしたのかを、ユーザーにわかりやすい短いタグ文言のリストで返す。

    quick_match()が使う検索条件(エリア・予算・想定用途)と物件データを突き合わせるだけの
    単純なルールベース。Gemini等は使わない。
    """
    reasons = [f"エリア「{area}」に一致"]
    price = p.get("price_man_yen")
    if max_budget is not None and price is not None and price <= max_budget:
        reasons.append("予算内")
    if use_type and use_type in (p.get("features") or ""):
        reasons.append(f"{use_type}向け")
    return reasons


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


@app.get("/diagnosis")
def diagnosis():
    return FileResponse(STATIC_DIR / "diagnosis.html")


@app.get("/chat")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health():
    return {"ok": True}


@app.post("/api/quick-match", response_model=QuickMatchResponse)
def quick_match(req: QuickMatchRequest):
    """診断フォームの回答から、Geminiとの会話を挟まずに直接マッチングを試みる(①診断の1段目)。

    エリアが未入力の場合はそもそも絞り込みができない(=ほぼ全件がヒットしてしまい「マッチング
    した」とは言えない)ため、常にマッチ無し扱いとし、フロント側でチャット(Geminiとの会話)に
    フォールバックさせる。
    """
    if not req.area or not req.area.strip():
        return QuickMatchResponse(matched=False, property_id=None, count=0, results=[])

    use_type = (req.dream or req.job or "").strip() or None
    max_budget = _parse_budget_man_yen(req.budget)

    result = tools.search_akiya(
        area=req.area.strip(),
        max_budget_man_yen=max_budget,
        use_type=use_type,
        limit=3,
    )
    results = result.get("results", [])
    for p in results:
        p["match_reasons"] = _match_reasons(p, req.area.strip(), max_budget, use_type)
    if results:
        subsidies: list[dict] = []
        if req.want_subsidy:
            # フォームで「補助金も知りたい」が選ばれた時だけ、マッチした物件のエリア・市区町村に
            # 関連する補助金・支援制度を探す。Geminiは介さずキーワード一致だけなので
            # APIクオータは消費しない。
            subsidies_by_id: dict[int, dict] = {}
            seen_areas: set[str] = set()
            for p in results:
                for area_key in (p.get("municipality"), p.get("area")):
                    if not area_key or area_key in seen_areas:
                        continue
                    seen_areas.add(area_key)
                    for s in tools.search_subsidies_for_area(area_key).get("results", []):
                        subsidies_by_id[s["id"]] = s
            subsidies = list(subsidies_by_id.values())[:5]
        return QuickMatchResponse(
            matched=True, property_id=results[0]["id"], count=len(results), results=results, subsidies=subsidies
        )
    return QuickMatchResponse(matched=False, property_id=None, count=0, results=[], subsidies=[])


@app.post("/api/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    if not req.message or not req.message.strip():
        raise HTTPException(status_code=400, detail="message は必須です。")

    session_id, agent = _get_or_create_agent(req.session_id)
    try:
        result = agent.send(req.message)
    except DailyQuotaExceededError as e:
        # 1日あたりの無料枠を使い切った場合は、生の例外メッセージではなく
        # そのまま分かりやすい案内文を返す(すぐリトライしても解消しないため)。
        raise HTTPException(status_code=429, detail=str(e)) from e
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
