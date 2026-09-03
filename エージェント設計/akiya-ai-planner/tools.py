"""
tools.py
========
エージェントが function calling で呼び出す4つのツール(関数)の実装。

- search_akiya          : 空き家DB検索 (data/akiya_sample.json。B担当収集の実データ)
- estimate_renovation_cost : 改修コスト概算 (物件が実測値を持っていればそれを優先し、無ければ簡易式で概算)
- simulate_income       : 用途別の収支シミュレーション (簡易モデル)
- search_subsidies      : 補助金・支援制度の検索 (Gemini Embeddingsによる簡易RAG。APIキー無しの場合はキーワード検索にフォールバック)

B担当がDB(PostgreSQL)実装に移行する際は、この4関数のシグネチャ(引数名・返り値の形)を
変えずに中身だけ差し替えれば、agent.py側は無改修で動く設計にしています。
"""

from __future__ import annotations

import json
import math
import os
import re
from datetime import date
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).parent / "data"


# ---------------------------------------------------------------------------
# 共通ユーティリティ
# ---------------------------------------------------------------------------

def _load_json(filename: str) -> dict:
    with open(DATA_DIR / filename, encoding="utf-8") as f:
        return json.load(f)


def _akiya_records() -> list[dict]:
    return _load_json("akiya_sample.json")["properties"]


def _subsidy_records() -> list[dict]:
    return _load_json("subsidies_sample.json")["subsidies"]


# ---------------------------------------------------------------------------
# 1. 空き家DB検索
# ---------------------------------------------------------------------------

_ROOM_COUNT_RE = re.compile(r"(\d+)\s*(?:S?LDK|DK|K)")


def _estimated_room_count(layout: str | None) -> int | None:
    """layout文字列("4LDK"や"3K"など)から部屋数の目安を抽出する。取れなければNone。"""
    if not layout:
        return None
    m = _ROOM_COUNT_RE.search(layout)
    return int(m.group(1)) if m else None


def search_akiya(
    area: str | None = None,
    max_budget_man_yen: float | None = None,
    min_budget_man_yen: float | None = None,
    use_type: str | None = None,
    family_size: int | None = None,
    limit: int = 5,
) -> dict:
    """条件に合う空き家候補を検索する。

    Args:
        area: エリア名・住所の一部(例: "千葉県", "奥多摩町", "羽生市"など)。部分一致。
        max_budget_man_yen: 予算上限(万円)。価格未掲載(応相談)の物件は上限フィルタでは除外しない。
        min_budget_man_yen: 予算下限(万円)。
        use_type: 想定用途(例: "カフェ", "民泊", "ゲストハウス", "店舗", "移住"など)。物件の特徴文(features)から
            それらしいキーワードを含む物件を優先的に上位表示する(タグではなく自由文からのスコアリング)。
        family_size: 想定居住人数。2人以上ならLDK数が多め(3部屋以上)の物件を優先。
        limit: 返す件数の上限(デフォルト5件)。

    Returns:
        {"count": int, "results": [物件dict, ...]} 形式。
        各物件dictには id, title, municipality, area(住所), lat, lng, price_man_yen(nullの場合あり),
        layout, floors, land_area_sqm, building_area_sqm, structure, built_year,
        renovation_cost_est_man_yen(物件データに基づく改修費目安。nullの場合あり), features, match_score を含む。
    """
    records = _akiya_records()
    matched: list[tuple[float, dict]] = []

    for rec in records:
        # ハード条件(満たさなければ除外)
        if area and area not in rec["area"] and area not in rec.get("municipality", ""):
            continue
        price = rec.get("price_man_yen")
        if price is not None:
            if max_budget_man_yen is not None and price > max_budget_man_yen:
                continue
            if min_budget_man_yen is not None and price < min_budget_man_yen:
                continue

        # ソフトスコア(並び替え用)
        score = 0.0
        features = rec.get("features", "") or ""
        if use_type:
            if use_type in features:
                score += 3
            # 用途に関連しがちなキーワードでの緩い一致もボーナス
            for kw in ("カフェ", "民泊", "ゲストハウス", "店舗", "移住", "シェア"):
                if kw in use_type and kw in features:
                    score += 1
        if family_size is not None:
            rooms = _estimated_room_count(rec.get("layout"))
            if rooms is not None:
                wants_family = family_size >= 2
                if wants_family and rooms >= 3:
                    score += 2
                if not wants_family and rooms <= 2:
                    score += 2
        # 予算に近い(安すぎず高すぎない)ものを少し優遇。価格未掲載は中立(0)扱い。
        if max_budget_man_yen and price is not None:
            score += max(0.0, 1 - abs(price - max_budget_man_yen) / max_budget_man_yen)

        matched.append((score, rec))

    def _sort_key(item: tuple[float, dict]) -> tuple[float, float]:
        score, rec = item
        price = rec.get("price_man_yen")
        return (-score, price if price is not None else float("inf"))

    matched.sort(key=_sort_key)
    results = []
    for score, rec in matched[:limit]:
        item = dict(rec)
        item["match_score"] = round(score, 2)
        results.append(item)

    return {"count": len(results), "results": results}


def get_akiya_by_id(property_id: int) -> dict | None:
    """物件IDから1件取得する(内部ヘルパー。estimate_renovation_costから利用)。"""
    for rec in _akiya_records():
        if rec.get("id") == property_id:
            return rec
    return None


# ---------------------------------------------------------------------------
# 2. 改修コスト概算
# ---------------------------------------------------------------------------

_BASE_COST_PER_SQM_MAN_YEN = {
    "木造": 15.0,
    "鉄骨造": 18.0,
    "RC造": 22.0,
}
_CONDITION_FACTOR = {
    "良好": 0.8,
    "普通": 1.0,
    "要修繕": 1.3,
    "老朽化": 1.6,
}
_USE_TYPE_ADDON_MAN_YEN = {
    "カフェ": 250,
    "ゲストハウス": 300,
    "民泊": 300,
    "シェアハウス": 150,
}


def estimate_renovation_cost(
    property_id: int | None = None,
    building_area_sqm: float | None = None,
    built_year: int | None = None,
    structure: str = "木造",
    condition: str = "普通",
    use_type: str | None = None,
) -> dict:
    """改修コストを概算する。

    property_id を指定した場合、その物件についてB担当が収集した実測ベースの改修費目安
    (renovation_cost_est_man_yen)があればそれを優先して返す。無ければ、築年数(不明な場合は
    築年数不明として簡易式で仮定)・広さ・構造から概算する。

    Args:
        property_id: search_akiyaで得た物件のid。指定すると物件データの実測値を優先利用する。
        building_area_sqm: 建物の延床面積(平米)。property_id未指定時は必須。
        built_year: 築年(西暦)。空き家バンクの多くは築年不詳のため省略可(不明として概算)。
        structure: 構造("木造"/"鉄骨造"/"RC造")。property_id指定時は物件データの値を優先。
        condition: 現況("良好"/"普通"/"要修繕"/"老朽化")。
        use_type: 想定用途。店舗・宿泊系は水回り等の追加工事費を加算する。

    Returns:
        estimated_cost_man_yen, cost_range_man_yen(min/max), breakdown, note を含むdict。
    """
    if property_id is not None:
        rec = get_akiya_by_id(property_id)
        if rec is not None:
            if building_area_sqm is None:
                building_area_sqm = rec.get("building_area_sqm")
            if rec.get("structure") and rec["structure"] != "-":
                structure = rec["structure"]
            real_est = rec.get("renovation_cost_est_man_yen")
            if real_est:
                return {
                    "estimated_cost_man_yen": real_est,
                    "cost_range_man_yen": {"min": round(real_est * 0.85), "max": round(real_est * 1.15)},
                    "breakdown": {"source": "物件データ提供の実測ベース概算(B担当収集)"},
                    "note": f"物件ID {property_id} のデータに基づく改修費目安です(空き家バンク等の公開情報ベース)。",
                }

    if building_area_sqm is None:
        return {"error": "building_area_sqm が必要です(property_idで実測値が見つからない場合)。"}

    base_per_sqm = _BASE_COST_PER_SQM_MAN_YEN.get(structure, 15.0)
    if built_year is not None:
        age = max(0, date.today().year - built_year)
        age_note = ""
    else:
        # 空き家バンク物件は築年不詳が多い。中古の空き家として築35年程度を仮定する。
        age = 35
        age_note = "(築年不明のため築35年と仮定)"
    age_factor = 1 + min(age, 60) / 60 * 0.8
    condition_factor = _CONDITION_FACTOR.get(condition, 1.0)

    base_cost = base_per_sqm * building_area_sqm * age_factor * condition_factor

    addon = 0.0
    if use_type:
        for key, val in _USE_TYPE_ADDON_MAN_YEN.items():
            if key in use_type:
                addon = val
                break

    total = base_cost + addon
    return {
        "estimated_cost_man_yen": round(total),
        "cost_range_man_yen": {"min": round(total * 0.8), "max": round(total * 1.2)},
        "breakdown": {
            "base_cost_man_yen": round(base_cost),
            "use_type_addon_man_yen": round(addon),
            "age_years": age,
            "age_factor": round(age_factor, 2),
            "condition_factor": condition_factor,
            "base_cost_per_sqm_man_yen": base_per_sqm,
        },
        "note": f"簡易ヒューリスティック式による概算です{age_note}。実際の見積りとは異なる場合があります。",
    }


# ---------------------------------------------------------------------------
# 3. 収支シミュレーション
# ---------------------------------------------------------------------------

def simulate_income(
    use_type: str,
    building_area_sqm: float,
    renovation_cost_man_yen: float | None = None,
    capacity: int | None = None,
    location_type: str = "地方",
) -> dict:
    """想定用途ごとの月次収支・投資回収年数を簡易シミュレーションする。

    Args:
        use_type: 想定用途("カフェ", "シェアハウス", "民泊"/"ゲストハウス", それ以外は賃貸モデル)。
        building_area_sqm: 建物の延床面積(平米)。
        renovation_cost_man_yen: 改修コスト概算(万円)。投資回収年数の算出に使用。
        capacity: シェアハウスの部屋数、または民泊の収容人数(ベッド数)など。未指定時は面積から概算。
        location_type: "都市部" or "地方"。地方は単価をやや下げる。

    Returns:
        monthly_revenue_man_yen, monthly_cost_man_yen, monthly_profit_man_yen,
        annual_profit_man_yen, payback_period_years, assumptions, note を含むdict。
    """
    is_urban = location_type == "都市部"
    assumptions: dict[str, Any] = {}

    if "カフェ" in use_type:
        tsubo = building_area_sqm / 3.3
        seats = max(4, round(tsubo * 1.2))
        avg_spend_yen = 900 if is_urban else 750
        turnover = 2.0 if is_urban else 1.5
        operating_days = 25
        monthly_revenue = seats * avg_spend_yen * turnover * operating_days / 10000  # 万円
        material_cost_ratio = 0.30
        fixed_cost_man_yen = 15 if is_urban else 10  # 光熱費・通信費等(自己所有前提で家賃なし)
        monthly_cost = monthly_revenue * material_cost_ratio + fixed_cost_man_yen
        assumptions = {"seats": seats, "avg_spend_yen": avg_spend_yen, "turnover_per_day": turnover, "operating_days": operating_days}

    elif "シェアハウス" in use_type:
        rooms = capacity or max(2, round(building_area_sqm / 15))
        rent_per_room_man_yen = 3.2 if is_urban else 2.5
        occupancy_rate = 0.85
        monthly_revenue = rooms * rent_per_room_man_yen * occupancy_rate
        monthly_cost = rooms * 0.3 + 3  # 共用部光熱費・管理費目安
        assumptions = {"rooms": rooms, "rent_per_room_man_yen": rent_per_room_man_yen, "occupancy_rate": occupancy_rate}

    elif "民泊" in use_type or "ゲストハウス" in use_type:
        beds = capacity or max(2, round(building_area_sqm / 10))
        adr_yen = 8000 if is_urban else 6000  # 1泊あたり平均単価
        occupancy_rate = 0.45
        monthly_revenue = beds * adr_yen * occupancy_rate * 30 / 10000
        monthly_cost = monthly_revenue * 0.25 + 8  # 清掃・OTA手数料・光熱費目安
        assumptions = {"beds": beds, "adr_yen": adr_yen, "occupancy_rate": occupancy_rate}

    else:
        rent_per_sqm_yen = 1800 if is_urban else 1200
        monthly_revenue = building_area_sqm * rent_per_sqm_yen / 10000
        monthly_cost = monthly_revenue * 0.1 + 2
        assumptions = {"rent_per_sqm_yen": rent_per_sqm_yen, "model": "賃貸(その他用途デフォルト)"}

    monthly_profit = monthly_revenue - monthly_cost
    annual_profit = monthly_profit * 12

    payback_period_years = None
    if renovation_cost_man_yen and annual_profit > 0:
        payback_period_years = round(renovation_cost_man_yen / annual_profit, 1)

    return {
        "monthly_revenue_man_yen": round(monthly_revenue, 1),
        "monthly_cost_man_yen": round(monthly_cost, 1),
        "monthly_profit_man_yen": round(monthly_profit, 1),
        "annual_profit_man_yen": round(annual_profit, 1),
        "payback_period_years": payback_period_years,
        "assumptions": assumptions,
        "note": "簡易モデルによる試算です。実際の集客・稼働率により大きく変動します。",
    }


# ---------------------------------------------------------------------------
# 4. 補助金検索 (RAG風。Embeddingsが使えなければキーワード検索にフォールバック)
# ---------------------------------------------------------------------------

_embedding_cache: dict[str, list[float]] | None = None


def _cosine_sim(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _try_embedding_search(query: str, records: list[dict], top_k: int) -> list[tuple[float, dict]] | None:
    """Gemini Embeddings APIでの意味検索を試みる。失敗時はNoneを返す(呼び出し側でフォールバック)。"""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return None
    try:
        from google import genai  # 遅延importでAPIキー無し環境でも本モジュール自体は読み込める

        global _embedding_cache
        client = genai.Client(api_key=api_key)

        if _embedding_cache is None:
            _embedding_cache = {}
        texts = []
        ids_needing_embed = []
        for rec in records:
            if rec["id"] not in _embedding_cache:
                ids_needing_embed.append(rec["id"])
                texts.append(f"{rec['title']} {rec.get('keywords', '')} {' '.join(rec.get('tags', []))} {rec.get('area', '')}")
        if texts:
            resp = client.models.embed_content(model="text-embedding-004", contents=texts)
            embeddings = resp.embeddings if hasattr(resp, "embeddings") else [resp.embedding]
            for rec_id, emb in zip(ids_needing_embed, embeddings):
                vec = emb.values if hasattr(emb, "values") else emb
                _embedding_cache[rec_id] = list(vec)

        query_resp = client.models.embed_content(model="text-embedding-004", contents=[query])
        q_emb = query_resp.embeddings[0] if hasattr(query_resp, "embeddings") else query_resp.embedding
        q_vec = list(q_emb.values if hasattr(q_emb, "values") else q_emb)

        scored = [(_cosine_sim(q_vec, _embedding_cache[rec["id"]]), rec) for rec in records]
        scored.sort(key=lambda x: -x[0])
        return scored[:top_k]
    except Exception:
        return None


def _keyword_search(query: str, area: str | None, records: list[dict], top_k: int) -> list[tuple[float, dict]]:
    tokens = [t for t in query.replace(",", " ").replace("、", " ").split() if t]
    if not tokens:
        tokens = [query] if query else []

    scored = []
    for rec in records:
        text = " ".join([
            rec.get("title", ""),
            rec.get("keywords", ""),
            " ".join(rec.get("tags", [])),
            rec.get("area", ""),
            " ".join(rec.get("target", [])),
        ])
        score = sum(1 for t in tokens if t in text)
        if area:
            rec_area = rec.get("area", "")
            if area in rec_area or "全国" in rec_area:
                score += 1
        scored.append((float(score), rec))

    scored.sort(key=lambda x: -x[0])
    return scored[:top_k]


def search_subsidies(query: str, area: str | None = None, top_k: int = 3) -> dict:
    """関連する補助金・支援制度を検索する(RAG風の簡易検索)。

    GEMINI_API_KEY が設定されていればEmbeddingsによる意味検索を行い、
    未設定または呼び出し失敗時はキーワード一致による検索にフォールバックする。

    Args:
        query: 検索クエリ。スペース区切りのキーワードを複数渡すと精度が上がる
            (例: "カフェ 改修 千曲市")。
        area: エリア名(例: "千曲市")。指定すると当該エリア向け制度・全国制度を優先。
        top_k: 返す件数(デフォルト3件)。

    Returns:
        {"count": int, "method": "embedding"|"keyword", "results": [補助金dict, ...]}
    """
    records = _subsidy_records()

    scored = _try_embedding_search(query, records, top_k)
    method = "embedding"
    if scored is None:
        scored = _keyword_search(query, area, records, top_k)
        method = "keyword"

    results = []
    for score, rec in scored:
        if score <= 0:
            continue
        item = dict(rec)
        item["relevance_score"] = round(score, 3)
        results.append(item)

    return {"count": len(results), "method": method, "results": results}


if __name__ == "__main__":
    # 簡易動作確認(APIキーなしでも実行可能)
    print("=== search_akiya ===")
    result = search_akiya(area="千葉県", max_budget_man_yen=500, use_type="カフェ")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    first_id = result["results"][0]["id"] if result["results"] else None

    print("\n=== estimate_renovation_cost (property_id指定・実測値優先) ===")
    if first_id is not None:
        print(json.dumps(estimate_renovation_cost(property_id=first_id, use_type="カフェ"), ensure_ascii=False, indent=2))

    print("\n=== estimate_renovation_cost (property_id無し・簡易式) ===")
    print(json.dumps(estimate_renovation_cost(building_area_sqm=100, use_type="カフェ"), ensure_ascii=False, indent=2))

    print("\n=== simulate_income ===")
    print(json.dumps(simulate_income(use_type="カフェ", building_area_sqm=100, renovation_cost_man_yen=500), ensure_ascii=False, indent=2))

    print("\n=== search_subsidies ===")
    print(json.dumps(search_subsidies(query="カフェ 改修 千葉県", area="千葉県"), ensure_ascii=False, indent=2))
