"""
image_gen.py
============
空き家の「活用イメージ」画像を生成するヘルパー。

エージェント設計/akiya-ai-planner/tools.py の generate_shop_image と同じ
Gemini画像生成モデルの呼び出し方針を踏襲しつつ、こちらは物件データ(houses テーブル)
が持つ実際の特徴(構造・築年・間取り・面積・features等)をプロンプトに組み込むことで、
「選んだ物件 × 選んだ用途 × 選んだ雰囲気」で見た目が変わる画像を生成する。

B担当側の都合でPDF出力アプリとチャットアプリ(akiya-ai-planner)は別デプロイの
サービスのため、依存を増やさないようにこのファイル単体で完結させている
(tools.pyをimportしない)。

生成の流れ(非同期ポーリング方式):
    以前は client.interactions.create() の完了をこのプロセス内でブロッキング待機する
    実装だったが、1回のHTTPリクエスト(ブラウザ→Render→Gemini)が数十秒〜170秒ほど
    張りっぱなしになるため、Render側やブラウザ側のインフラがその間に接続を切ってしまい
    「タイムアウト/エラーになる」事象が実機で頻発していた。

    そこで、1回のHTTPリクエストは必ずすぐ(数秒以内)に返す2段階方式に変更する。
    1) start_house_image_generation(): interactions.create(background=True) を呼んで
       interaction_id だけをすぐ返す(生成の完了は待たない)。
    2) check_house_image_generation(interaction_id): interactions.get() を1回呼んで
       今の状態を返すだけの、ブロッキングしない軽い関数。フロント側がこれを数秒おきに
       呼び出す(ポーリングする)ことで、完了を待つ長時間接続はブラウザ↔Render間には
       一切発生しなくなる。
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any


def _log(message: str) -> None:
    """Renderのログにタイムスタンプ付きで即座に出す(gunicornのバッファリング対策でflush)。"""
    print(f"[image_gen] {time.strftime('%H:%M:%S')} {message}", file=sys.stderr, flush=True)


_DEFAULT_IMAGE_MODEL = "gemini-3.1-flash-image"

# create/get 1回ごとのHTTPリクエストに対するタイムアウト。短く保つことで、
# 仮に1回のリクエストがネットワーク的に固まっても長時間ブロックされず、
# 次のポーリングでリトライできるようにする。
_PER_REQUEST_TIMEOUT_SECONDS = 20.0

_TERMINAL_STATUSES = {"completed", "failed", "cancelled", "incomplete", "budget_exceeded"}


def _describe_house(house: dict[str, Any]) -> str:
    """物件データから、画像生成プロンプトに使う建物の特徴の説明文を組み立てる。"""
    bits: list[str] = []

    structure = house.get("structure")
    if structure and structure != "-":
        bits.append(f"構造は{structure}")

    built_year = house.get("built_year")
    if built_year and built_year not in ("-", "不詳"):
        bits.append(f"築年は{built_year}")

    layout = house.get("layout")
    if layout and layout != "-":
        bits.append(f"間取りは{layout}")

    building_area = house.get("building_area_m2")
    if building_area:
        bits.append(f"建物面積は約{building_area}平米")

    floors = house.get("floors")
    if floors:
        bits.append(f"{floors}階建て")

    address = house.get("address") or f"{house.get('prefecture', '')}{house.get('municipality', '')}"
    if address:
        bits.append(f"所在地は{address}")

    features = house.get("features")
    if features:
        bits.append(f"物件の特徴: {features}")

    return "。".join(bits) if bits else "詳細情報は不明な古い空き家"


def _build_prompt(house: dict[str, Any], use_type: str, mood: str | None) -> str:
    house_desc = _describe_house(house)
    house_name = house.get("title") or house.get("name") or "この空き家"
    mood_text = f"雰囲気・テイストは「{mood}」にしてください。" if mood else ""

    return (
        f"日本の空き家「{house_name}」を、「{use_type}」として活用した場合の外観・内観イメージを"
        "1枚のイラスト風または写真的な画像で生成してください。"
        f"この空き家の実際の特徴: {house_desc}。"
        f"{mood_text}"
        "空き家ならではの古さ・味わいを活かしつつ、活用後の明るく親しみやすい雰囲気にしてください。"
    )


def _friendly_error(e: Exception) -> str:
    message = str(e)
    if "429" in message or "Too Many Requests" in message:
        return "現在リクエストが混み合っています(レート制限)。1分ほど待ってからもう一度お試しください。"
    if "403" in message or "Forbidden" in message:
        return "画像生成への権限がありません(APIキーの設定をご確認ください)。"
    return f"画像生成中にエラーが発生しました: {message}"


def start_house_image_generation(house: dict[str, Any], use_type: str, mood: str | None = None) -> dict:
    """この空き家を指定の用途で活用した場合の「活用イメージ」画像の生成を開始する。

    interactions.create(background=True) を呼ぶだけで、生成の完了は待たずにすぐ返す。
    呼び出し元(Flaskルート)はこの戻り値の interaction_id をそのままフロントに返し、
    フロント側は check_house_image_generation() を叩くエンドポイントを数秒おきに
    ポーリングして完了を待つ。

    Args:
        house: housesテーブルの1件分のdict(title/structure/built_year/layout/
            building_area_m2/floors/address/features等を含む)。
        use_type: どんな用途で使いたいか(例: "カフェ", "シェアハウス", "ギャラリー"。
            決まった選択肢に限らず自由記述を想定)。
        mood: 雰囲気・テイストの指定(例: "北欧風ナチュラル", "和モダンで落ち着いた雰囲気"。
            省略可)。

    Returns:
        成功時: {"interaction_id": str, "status": str, "prompt_used": str}
        失敗時: {"error": str, "prompt_used": str}
    """
    _log(
        f"start_house_image_generation呼び出し house_id={house.get('global_id') or house.get('id')} "
        f"use_type={use_type} mood={mood}"
    )

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return {"error": "GEMINI_API_KEY が設定されていないため、画像を生成できません。"}

    full_prompt = _build_prompt(house, use_type, mood)

    try:
        from google import genai  # 遅延import(APIキー無し環境でもモジュール自体は読み込めるように)

        client = genai.Client(api_key=api_key)
        model = os.environ.get("GEMINI_IMAGE_MODEL", _DEFAULT_IMAGE_MODEL)

        _t_start = time.monotonic()
        _log(f"client.interactions.create(background=True) 呼び出し開始 model={model}")
        interaction = client.interactions.create(
            model=model,
            input=full_prompt,
            background=True,
            timeout=_PER_REQUEST_TIMEOUT_SECONDS,
        )
        _log(
            f"作成リクエスト完了 id={interaction.id} status={interaction.status} "
            f"経過={time.monotonic() - _t_start:.1f}秒"
        )
        return {
            "interaction_id": interaction.id,
            "status": interaction.status,
            "prompt_used": full_prompt,
        }
    except Exception as e:  # noqa: BLE001
        _log(f"生成開始で例外発生: {type(e).__name__}: {e}")
        return {"error": _friendly_error(e), "prompt_used": full_prompt}


def check_house_image_generation(interaction_id: str) -> dict:
    """生成中のinteractionの「今の状態」を1回だけ確認する(ブロッキングしない)。

    フロント側がこの関数を使うエンドポイントを数秒おきに呼び出すことを想定している。
    1回のHTTPリクエスト(ブラウザ→Render→Gemini)がごく短時間で返るため、
    Render側やブラウザ側の中継がどこかに持つかもしれないアイドルタイムアウトの
    影響を受けない。

    Returns:
        進行中: {"status": "in_progress"}
        完了: {"status": "completed", "image_base64": str, "mime_type": str}
        失敗: {"status": "failed", "error": str}
    """
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return {"status": "failed", "error": "GEMINI_API_KEY が設定されていないため、画像を生成できません。"}

    try:
        from google import genai

        client = genai.Client(api_key=api_key)
        interaction = client.interactions.get(interaction_id, timeout=_PER_REQUEST_TIMEOUT_SECONDS)
    except Exception as e:  # noqa: BLE001
        # ポーリング1回の失敗はネットワーク的な一時エラーのことが多いので、ここでは
        # 「失敗」と決めつけず「まだ進行中」として扱い、フロント側の次のポーリングで
        # リトライさせる(フロント側の全体タイムアウトに達したら諦める)。
        _log(f"状態確認ポーリングで例外発生(リトライ対象): {type(e).__name__}: {e}")
        return {"status": "in_progress"}

    if interaction.status not in _TERMINAL_STATUSES:
        return {"status": "in_progress"}

    if interaction.status == "completed":
        image = getattr(interaction, "output_image", None)
        if image is not None and getattr(image, "data", None):
            _log(f"画像データ取得成功 interaction_id={interaction_id}")
            return {
                "status": "completed",
                "image_base64": image.data,
                "mime_type": getattr(image, "mime_type", None) or "image/png",
            }
        _log(f"status=completedだが画像データなし interaction_id={interaction_id}")
        return {
            "status": "failed",
            "error": "画像を生成できませんでした(モデルから画像が返されませんでした)。",
        }

    errors = getattr(interaction, "errors", None)
    _log(f"生成失敗 interaction_id={interaction_id} status={interaction.status} errors={errors}")
    return {
        "status": "failed",
        "error": f"画像生成に失敗しました(status={interaction.status})。しばらく待ってからもう一度お試しください。",
    }
