"""
agent.py
========
「空き家AIプランナー」の対話エージェント本体。

Gemini API の function calling を使い、ユーザーとの対話の中で
tools.py の4関数(空き家検索・改修コスト概算・収支シミュレーション・補助金検索)を
自律的に呼び出しながら提案をまとめる。

使い方:
    from agent import AkiyaAgent
    agent = AkiyaAgent()
    result = agent.send("千葉県でカフェを開きたい。予算は300万円くらい。")
    print(result.reply)
    print(result.tool_calls)  # 呼び出されたツールと結果のログ(地図・グラフ表示用にも使える)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

import time

from dotenv import load_dotenv
from google import genai
from google.genai import errors as genai_errors
from google.genai import types

import tools

load_dotenv()

# Gemini側が混雑している時(503)やレート制限(429)は、一時的なものであることが多いため、
# 少し待って自動的にリトライする。ユーザーには「考え中」のまま見えるだけで、失敗を意識させない。
_RETRYABLE_CODES = {429, 503}
_MAX_RETRIES = 3
_RETRY_BASE_DELAY_SEC = 2.0


def _send_with_retry(chat, content):
    """chat.send_message を、一時的なエラー(429/503)であれば待機して再試行しながら呼び出す。"""
    last_error: Exception | None = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            return chat.send_message(content)
        except genai_errors.APIError as e:
            code = getattr(e, "code", None)
            if code in _RETRYABLE_CODES and attempt < _MAX_RETRIES:
                last_error = e
                time.sleep(_RETRY_BASE_DELAY_SEC * (2 ** attempt))
                continue
            raise
    raise last_error  # pragma: no cover (ここには到達しない想定)


# ---------------------------------------------------------------------------
# システムプロンプト
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
あなたは「空き家AIプランナー」という対話エージェントです。
「移住したい」「お店を開きたい」といったユーザーの漠然とした希望を、対話を通じて
具体的な空き家活用プランに変えるのがあなたの役割です。

対話は次の4つのフェーズで進めてください。ユーザーの発言によっては途中のフェーズから
始まったり、フェーズを行き来したりして構いません(厳密な一問一答形式にする必要はありません)。

# フェーズ① 診断(自分の理想・適性を知る)
まず、次のような観点でユーザーの希望や人柄をヒアリングします。一度に聞く質問は1〜2個までにし、
圧迫感を与えないようにしてください。
  - 職業・やりたい仕事、興味のある働き方
  - 性格・好きなこと、趣味
  - 住みたい環境(都会寄り/田舎/海の近く/山の近くなど)
  - 理想の暮らし方
  - 「カフェを開きたい」「農業をしたい」「移住してのんびり暮らしたい」といった夢・やりたいこと
全項目が揃うまで待つ必要はありません。エリアと(用途または予算)のどちらかが分かれば、
フェーズ②に進んで search_akiya を実行して構いません。

# フェーズ② マッチング(自分に合った空き家を提案)
1. search_akiya で条件に合う空き家候補を検索し、上位2〜3件を「あなたにはこの空き家が
   おすすめです」という形で、価格・広さ・特徴とともに分かりやすく提示します。
2. 提示する際は、物件データ(価格・広さ・構造・特徴)に加えて、その地域の周辺環境
   (人口規模・交通アクセス、観光地、スーパー・病院・学校などの生活環境、その地域で
   できそうな仕事・産業)についても触れ、暮らしのイメージが湧くようにしてください。
   これらの周辺環境情報はデータベースには含まれていないため、あなたの一般知識をもとに
   説明し、「※周辺情報はAIの一般的な知識に基づく参考情報であり、最新の正確な情報は
   自治体や現地で確認してください」といった趣旨の断り書きを必ず添えてください。
   知らない・確信が持てない場合は、断定せず「〜と言われています」「一般的には〜」など
   幅を持たせた表現にしてください。
3. 「予算をもう少し下げたい」「もっと広い家がいい」など追加の要望があれば、条件を更新して
   search_akiya を再実行してください。

# フェーズ③ AI提案(この空き家で何ができるかを具体化)
ユーザーが候補に興味を示し、やりたいこと(例:「カフェを開きたい」)が見えてきたら、
その物件と想定用途に合わせて具体的な活用プランを提案します。
  - コンセプト
  - おすすめのターゲット層
  - メニュー案・サービス案
  - 店名・屋号のアイデア
  - 店舗やお店の雰囲気(内装・外観のイメージ)
  - ロゴのイメージ(色・モチーフなど)
  - SNSでのPR方法
  雰囲気・ロゴのイメージが具体的に固まったら、generate_shop_image を呼び出して、実際に
  店舗イメージ画像やロゴ案の画像を1枚生成し、ユーザーに見せてください。promptには
  コンセプト・雰囲気・色味・モチーフなど具体的な内容を渡し、image_typeには
  「店舗イメージ」「ロゴ」などその画像が何かを渡してください。画像生成に失敗した場合
  (結果にerrorが含まれる場合)は、無理に再試行せず、代わりに文章で具体的に描写して
  補ってください。生成した画像はあくまでAIによるイメージ案であり、実際の店舗デザインを
  保証するものではないことを伝えてください。
  あわせて、estimate_renovation_cost で改修費用を、simulate_income で収支シミュレーションを
  行い、結果を分かりやすく説明します。
  さらに search_subsidies でエリアや用途に関連する補助金・支援制度を検索し、使えそうな
  制度を提示します。

# フェーズ④ 暮らすメリット(ここで暮らす・営業するメリットのまとめ)
候補が1つに絞られてきたら、実データ(物件情報・改修コスト・収支・補助金)と、フェーズ②で
触れた周辺環境の一般知識を組み合わせて、「この家(この地域)で暮らす・お店を開くメリット」を
まとめて提示してください。例えば次のような形式が分かりやすいです(内容は物件・用途に応じて
調整してください)。
  例)
  🏠 この家なら
  ・〇〇(想定用途)を始められる可能性があります
  ・最寄り駅まで車で〇分(分かる範囲で)
  ・観光客が多い/自然が豊かな地域
  ・近隣に競合が少ない
  ・家賃・価格が抑えめで、店舗兼住宅としても利用しやすい
このサマリーが、最終的なPDF提案書のもとになります(PDF出力自体は別モジュールが行います)。

# 対応可能エリアについて
現在データベースに登録されている空き家は、千葉県内の各市町村・東京都西多摩地域(奥多摩町・
青梅市・あきる野市など)・埼玉県羽生市が中心です。これら以外のエリアを希望された場合は、
正直に「現在このエリアのデータは未登録です」と伝えた上で、対応可能なエリアを案内してください。

# 注意事項
- 数値(改修コスト・収支)はすべて概算であることを必ず明示してください。特に改修コストは、
  物件データに実測ベースの目安がある場合(estimate_renovation_costにproperty_idを渡した場合)は
  そのことも伝え、無い場合は簡易式による概算であることを伝えてください。
- 空き家データ・補助金データは実際の空き家バンク公開情報等をもとにしたものですが、価格・
  補助金の金額や条件は変更されている可能性があるため、最終判断の前に自治体等への確認を
  勧めてください。
- 人口・交通・観光地・生活環境・地域の仕事事情など、データベースに無い周辺情報はあなたの
  一般知識で補ってよいですが、必ず「一般的な参考情報である」ことが伝わるようにし、実データ
  (物件情報・補助金など)とは区別して説明してください。
- 物件によっては価格が「応相談」(price_man_yenがnull)の場合があります。その場合はその旨を
  伝え、断定的な金額を答えないでください。
- 店舗イメージ・ロゴの画像は generate_shop_image で生成できます。生成した画像は
  「AIが生成したイメージ案」であることを必ず伝え、実際の店舗の完成イメージを保証する
  ものではないと補足してください。生成に失敗した場合は、文章での描写に切り替えてください。
- ツールの引数は、ユーザーが実際に話した内容から具体的に埋めてください。話していない情報を
  勝手に断定しないでください(不明な場合は質問するか、ツールの該当引数を省略してください)。
- 常に丁寧で簡潔な日本語で応答してください。箇条書きは使いすぎず、会話らしい文章を心がけて
  ください(ただしフェーズ③・④の提案項目は、箇条書きの方が分かりやすければ使って構いません)。
"""


# ---------------------------------------------------------------------------
# ツール定義 (Gemini function calling 用のスキーマ)
# tools.py の実装と1対1で対応させる。引数名・必須項目を変えたらここも合わせて更新すること。
# ---------------------------------------------------------------------------

FUNCTION_DECLARATIONS = [
    types.FunctionDeclaration(
        name="search_akiya",
        description="条件に合う空き家候補をデータベースから検索する。エリアと予算・用途などの"
                    "分かっている条件だけを渡せばよい。",
        parameters_json_schema={
            "type": "object",
            "properties": {
                "area": {"type": "string", "description": "エリア名・住所の一部(例: 千葉県, 奥多摩町, 羽生市)"},
                "max_budget_man_yen": {"type": "number", "description": "予算上限(万円)"},
                "min_budget_man_yen": {"type": "number", "description": "予算下限(万円)"},
                "use_type": {"type": "string", "description": "想定用途(例: カフェ, 民泊, ゲストハウス, 店舗, 移住)。物件の特徴文からそれらしい記述がある物件を優先表示する。"},
                "family_size": {"type": "integer", "description": "想定居住人数"},
                "limit": {"type": "integer", "description": "返す件数の上限。指定なければ5件。"},
            },
        },
    ),
    types.FunctionDeclaration(
        name="estimate_renovation_cost",
        description="改修コストを概算する。物件ID(search_akiyaで得たid)を渡すと、その物件の実測"
                    "ベースの改修費目安があればそれを優先して返す。物件IDが無い場合や実測値が無い"
                    "場合は、広さ・構造・築年数(不明なら省略可)から簡易式で概算する。",
        parameters_json_schema={
            "type": "object",
            "properties": {
                "property_id": {"type": "integer", "description": "search_akiyaで得た物件のid。指定すると実測データを優先利用する。"},
                "building_area_sqm": {"type": "number", "description": "建物の延床面積(平米)。property_id未指定時は必須。"},
                "built_year": {"type": "integer", "description": "築年(西暦)。空き家バンク物件は築年不詳が多いので分からなければ省略してよい。"},
                "structure": {"type": "string", "enum": ["木造", "鉄骨造", "RC造"], "description": "構造"},
                "condition": {"type": "string", "enum": ["良好", "普通", "要修繕", "老朽化"], "description": "現況"},
                "use_type": {"type": "string", "description": "想定用途。店舗・宿泊系は追加工事費を加味する。"},
            },
        },
    ),
    types.FunctionDeclaration(
        name="simulate_income",
        description="想定用途ごとの月次収支と投資回収年数を簡易シミュレーションする。",
        parameters_json_schema={
            "type": "object",
            "properties": {
                "use_type": {"type": "string", "description": "想定用途(カフェ, シェアハウス, 民泊/ゲストハウス, その他は賃貸モデル)"},
                "building_area_sqm": {"type": "number", "description": "建物の延床面積(平米)"},
                "renovation_cost_man_yen": {"type": "number", "description": "改修コスト概算(万円)。投資回収年数の算出に使用。"},
                "capacity": {"type": "integer", "description": "シェアハウスの部屋数、または民泊の収容人数(ベッド数)"},
                "location_type": {"type": "string", "enum": ["都市部", "地方"], "description": "立地タイプ"},
            },
            "required": ["use_type", "building_area_sqm"],
        },
    ),
    types.FunctionDeclaration(
        name="search_subsidies",
        description="エリアや用途に関連する補助金・支援制度を検索する。",
        parameters_json_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "検索クエリ。スペース区切りのキーワードを複数渡すと精度が上がる(例: 'カフェ 改修 千葉県')"},
                "area": {"type": "string", "description": "エリア名(例: 千葉県, 奥多摩町)"},
                "top_k": {"type": "integer", "description": "返す件数。指定なければ3件。"},
            },
            "required": ["query"],
        },
    ),
    types.FunctionDeclaration(
        name="generate_shop_image",
        description="お店の店舗イメージ画像やロゴ案の画像を1枚生成する。コンセプトや雰囲気が具体的に"
                    "固まった段階(フェーズ③)で、ユーザーに見せる用に呼び出す。",
        parameters_json_schema={
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "生成したい画像の具体的な内容(コンセプト・雰囲気・色味・モチーフなど)"},
                "image_type": {"type": "string", "description": "何の画像か(例: 店舗イメージ, ロゴ, 外観イメージ)。指定なければ「店舗イメージ」。"},
            },
            "required": ["prompt"],
        },
    ),
]

TOOL_REGISTRY = {
    "search_akiya": tools.search_akiya,
    "estimate_renovation_cost": tools.estimate_renovation_cost,
    "simulate_income": tools.simulate_income,
    "search_subsidies": tools.search_subsidies,
    "generate_shop_image": tools.generate_shop_image,
}


# ---------------------------------------------------------------------------
# エージェント本体
# ---------------------------------------------------------------------------

@dataclass
class ToolCallLog:
    name: str
    args: dict[str, Any]
    result: dict[str, Any]


@dataclass
class AgentTurnResult:
    reply: str
    tool_calls: list[ToolCallLog] = field(default_factory=list)


class AkiyaAgent:
    """1ユーザー分の対話状態(履歴)を保持するエージェント。

    Web版では session_id ごとにインスタンスを1つ持たせる想定。
    """

    def __init__(self, api_key: str | None = None, model: str | None = None):
        api_key = api_key or os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "GEMINI_API_KEY が設定されていません。.env ファイルまたは環境変数で設定してください。"
            )
        self.client = genai.Client(api_key=api_key)
        self.model = model or os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")

        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            tools=[types.Tool(function_declarations=FUNCTION_DECLARATIONS)],
            # ツール実行はこちらで制御する(結果をログとして拾いたいため自動実行はしない)
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            temperature=0.4,
        )
        self.chat = self.client.chats.create(model=self.model, config=config)

    def _execute_tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        fn = TOOL_REGISTRY.get(name)
        if fn is None:
            return {"error": f"未知のツールです: {name}"}
        try:
            return fn(**args)
        except Exception as e:  # ツール側の想定外エラーでも対話を止めない
            return {"error": f"ツール実行中にエラーが発生しました: {e}"}

    def send(self, user_message: str, max_tool_iterations: int = 6) -> AgentTurnResult:
        """ユーザーの発話を送り、必要なツール呼び出しを内部で完結させた上で最終回答を返す。

        Gemini側が一時的に混雑している(503)場合やレート制限(429)の場合は、
        _send_with_retry が自動的に少し待って再試行する。
        """
        response = _send_with_retry(self.chat, user_message)
        tool_calls_log: list[ToolCallLog] = []

        iterations = 0
        while response.function_calls and iterations < max_tool_iterations:
            iterations += 1
            function_response_parts = []
            for fc in response.function_calls:
                args = dict(fc.args or {})
                result = self._execute_tool(fc.name, args)
                tool_calls_log.append(ToolCallLog(name=fc.name, args=args, result=result))
                function_response_parts.append(
                    types.Part.from_function_response(name=fc.name, response={"result": result})
                )
            response = _send_with_retry(self.chat, function_response_parts)

        reply = response.text or "(応答を生成できませんでした。もう一度お試しください)"
        return AgentTurnResult(reply=reply, tool_calls=tool_calls_log)


if __name__ == "__main__":
    # 簡易動作確認。GEMINI_API_KEY が必要。
    agent = AkiyaAgent()
    result = agent.send("千葉県でカフェを開きたいです。予算は300万円くらいです。")
    print(result.reply)
    for tc in result.tool_calls:
        print(f"[tool] {tc.name}({tc.args}) -> {tc.result}")
