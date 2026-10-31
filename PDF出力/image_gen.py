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
"""

from __future__ import annotations

import os
from typing import Any

_DEFAULT_IMAGE_MODEL = "gemini-3.1-flash-image"


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


def generate_house_image(house: dict[str, Any], use_type: str, mood: str | None = None) -> dict:
    """この空き家を指定の用途で活用した場合の「活用イメージ」画像を1枚生成する。

    Args:
        house: housesテーブルの1件分のdict(title/structure/built_year/layout/
            building_area_m2/floors/address/features等を含む)。
        use_type: どんな用途で使いたいか(例: "カフェ", "シェアハウス", "ギャラリー"。
            決まった選択肢に限らず自由記述を想定)。
        mood: 雰囲気・テイストの指定(例: "北欧風ナチュラル", "和モダンで落ち着いた雰囲気"。
            省略可)。

    Returns:
        成功時: {"image_base64": str, "mime_type": str, "prompt_used": str}
        失敗時: {"error": str, "prompt_used": str}
    """
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return {"error": "GEMINI_API_KEY が設定されていないため、画像を生成できません。"}

    house_desc = _describe_house(house)
    house_name = house.get("title") or house.get("name") or "この空き家"
    mood_text = f"雰囲気・テイストは「{mood}」にしてください。" if mood else ""

    full_prompt = (
        f"日本の空き家「{house_name}」を、「{use_type}」として活用した場合の外観・内観イメージを"
        "1枚のイラスト風または写真的な画像で生成してください。"
        f"この空き家の実際の特徴: {house_desc}。"
        f"{mood_text}"
        "空き家ならではの古さ・味わいを活かしつつ、活用後の明るく親しみやすい雰囲気にしてください。"
    )

    try:
        from google import genai  # 遅延import(APIキー無し環境でもモジュール自体は読み込めるように)
        from google.genai import types as genai_types

        # SDKのデフォルトは429(レート制限)等を最大5回・最長60秒リトライし続ける設定になっている。
        # Renderの無料枠はgunicornのワーカータイムアウトがあり、リトライで粘っている間に
        # ワーカーごと強制終了され、ユーザーには「反応なし」のまま固まって見えてしまう。
        # ここではリトライを無し(1回のみ)に絞り、全体のHTTPタイムアウトも短くして、
        # ダメなら数秒〜十数秒で諦めて分かりやすいエラーを返すようにする。
        client = genai.Client(
            api_key=api_key,
            http_options=genai_types.HttpOptions(
                timeout=25000,  # ミリ秒。gunicornのタイムアウトより十分短く設定。
                retry_options=genai_types.HttpRetryOptions(attempts=1),  # リトライ無し。
            ),
        )
        model = os.environ.get("GEMINI_IMAGE_MODEL", _DEFAULT_IMAGE_MODEL)

        interaction = client.interactions.create(model=model, input=full_prompt)

        image = getattr(interaction, "output_image", None)
        if image is not None and getattr(image, "data", None):
            return {
                "image_base64": image.data,
                "mime_type": getattr(image, "mime_type", None) or "image/png",
                "prompt_used": full_prompt,
            }
        return {
            "error": "画像を生成できませんでした(モデルから画像が返されませんでした)。",
            "prompt_used": full_prompt,
        }
    except Exception as e:  # noqa: BLE001
        message = str(e)
        if "429" in message or "Too Many Requests" in message:
            friendly = "現在リクエストが混み合っています(レート制限)。1分ほど待ってからもう一度お試しください。"
        elif "403" in message or "Forbidden" in message:
            friendly = "画像生成への権限がありません(APIキーの設定をご確認ください)。"
        else:
            friendly = f"画像生成中にエラーが発生しました: {message}"
        return {"error": friendly, "prompt_used": full_prompt}
