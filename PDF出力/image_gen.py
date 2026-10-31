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
import sys
import time
from typing import Any


def _log(message: str) -> None:
    """Renderのログにタイムスタンプ付きで即座に出す(gunicornのバッファリング対策でflush)。"""
    print(f"[image_gen] {time.strftime('%H:%M:%S')} {message}", file=sys.stderr, flush=True)


_DEFAULT_IMAGE_MODEL = "gemini-3.1-flash-image"

# 1回ごとのHTTPリクエスト(作成/ポーリング)に対するタイムアウト。短く保つことで、
# 仮に1回のリクエストがネットワーク的に固まっても、それも長時間ブロックされず、
# 次のポーリングでリトライできるようにする。
_PER_REQUEST_TIMEOUT_SECONDS = 20.0

# 画像生成が終わるのを待つ全体の上限時間。Render無料枠はスリープからの復帰で
# 最初のリクエストに50秒以上かかることがあるため、それも見込んでかなり長めに取る。
# gunicorn側のワーカータイムアウトはこれより長く設定しておくこと(Start Commandの
# --timeoutをこの値より十分大きくする)。
_MAX_WAIT_SECONDS = 170.0

_POLL_INTERVAL_SECONDS = 3.0

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
    _log(
        f"generate_house_image呼び出し house_id={house.get('global_id') or house.get('id')} "
        f"use_type={use_type} mood={mood}"
    )

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

        client = genai.Client(api_key=api_key)
        model = os.environ.get("GEMINI_IMAGE_MODEL", _DEFAULT_IMAGE_MODEL)

        # 以前は client.interactions.create() を1回のブロッキング呼び出しとして実行していたが、
        # 実機では90秒以上応答が返ってこない(SDK側のタイムアウト設定も効かない)事象が
        # 確認された。これは、長時間張りっぱなしのHTTP接続を途中の中継(プロキシ等)が
        # 応答なしに静かに落としてしまい、クライアント側が永遠に読み込み待ちになる、
        # という典型的な問題だと考えられる。
        #
        # そこで公式ドキュメント(ai.google.dev/gemini-api/docs/interactions)が長時間タスク向けに
        # 案内している background=True + ポーリング方式に切り替える。1回ごとのHTTPリクエストを
        # 短時間(_PER_REQUEST_TIMEOUT_SECONDS)で区切ることで、途中の接続が固まっても
        # そのリクエストだけがタイムアウトし、次のポーリングでやり直せるようにする。
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

        poll_count = 0
        while interaction.status not in _TERMINAL_STATUSES:
            elapsed = time.monotonic() - _t_start
            if elapsed >= _MAX_WAIT_SECONDS:
                _log(f"全体タイムアウト({_MAX_WAIT_SECONDS:.0f}秒経過、最終status={interaction.status})")
                return {
                    "error": "画像生成が時間内に完了しませんでした(タイムアウト)。しばらわ待ってからもう一度お試しください。",
                    "prompt_used": full_prompt,
                }
            time.sleep(_POLL_INTERVAL_SECONDS)
            poll_count += 1
            try:
                interaction = client.interactions.get(
                    interaction.id, timeout=_PER_REQUEST_TIMEOUT_SECONDS
                )
            except Exception as poll_error:  # noqa: BLE001
                # 1回のポーリングが失敗/タイムアウトしても、全体の時間内であればリトライする。
                _log(f"ポーリング#{poll_count}失敗(リトライ): {type(poll_error).__name__}: {poll_error}")
                continue
            _log(
                f"ポーリング#{poll_count} status={interaction.status} "
                f"経過={time.monotonic() - _t_start:.1f}秒"
            )

        if interaction.status == "completed":
            image = getattr(interaction, "output_image", None)
            if image is not None and getattr(image, "data", None):
                _log("画像データ取得成功、レスポンス返却")
                return {
                    "image_base64": image.data,
                    "mime_type": getattr(image, "mime_type", None) or "image/png",
                    "prompt_used": full_prompt,
                }
            _log("status=completedだが画像データなし")
            return {
                "error": "画像を生成できませんでした(モデルから画像が返されませんでした)。",
                "prompt_used": full_prompt,
            }

        errors = getattr(interaction, "errors", None)
        _log(f"生成失敗 status={interaction.status} errors={errors}")
        return {
            "error": f"画像生成に失敗しました(status={interaction.status})。しばらく待ってからもう一度お試しください。",
            "prompt_used": full_prompt,
        }
    except Exception as e:  # noqa: BLE001
        _log(f"例外発生: {type(e).__name__}: {e}")
        message = str(e)
        if "429" in message or "Too Many Requests" in message:
            friendly = "現在リクエストが混み合っています(レート制限)。1分ほど待ってからもう一度お試しください。"
        elif "403" in message or "Forbidden" in message:
            friendly = "画像生成への権限がありません(APIキーの設定をご確認ください)。"
        else:
            friendly = f"画像生成中にエラーが発生しました: {message}"
        return {"error": friendly, "prompt_used": full_prompt}
