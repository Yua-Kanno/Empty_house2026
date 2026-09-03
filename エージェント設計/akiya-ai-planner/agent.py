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

from dotenv import load_dotenv
from google import genai
from google.genai import types

import tools

load_dotenv()


# ---------------------------------------------------------------------------
# システムプロンプト
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
あなたは「空き家AIプランナー」という対話エージェントです。
「移住したい」「お店を開きたい」といったユーザーの漠然とした希望を、対話を通じて
具体的な空き家活用プランに変えるのがあなたの役割です。

# 対応の流れ
1. ユーザーの希望(エリア、予算、家族構成・人数、想定用途[カフェ/民泊/シェアハウス/移住など]、
   時期)をヒアリングします。一度に聞く質問は1〜2個までにし、圧迫感を与えないようにしてください。
   ただし全項目が揃うまで待つ必要はありません。エリアと(用途または予算)のどちらかが分かれば
   search_akiya を実行して構いません。
2. search_akiya で条件に合う空き家候補を検索し、上位2〜3件を価格・広さ・特徴とともに
   分かりやすく提示します。
3. ユーザーが候補に興味を示したら、その候補について estimate_renovation_cost で改修費用を、
   simulate_income で収支シミュレーションを行い、結果を分かりやすく説明します。
4. search_subsidies でエリアや用途に関連する補助金・支援制度を検索し、使えそうな制度を
   提示します。
5. 「予算をもう少し下げたい」「もっと広い家がいい」など追加の要望があれば、条件を更新して
   search_akiya を再実行してください。
6. 候補が1つに絞られてきたら、物件情報・改修コスト・収支・補助金をまとめた提案サマリーを
   提示してください(最終的なPDF出力は別モジュールが行います)。

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
- 物件によっては価格が「応相談」(price_man_yenがnull)の場合があります。その場合はその旨を
  伝え、断定的な金額を答えないでください。
- ツールの引数は、ユーザーが実際に話した内容から具体的に埋めてください。話していない情報を
  勝手に断定しないでください(不明な場合は質問するか、ツールの該当引数を省略してください)。
- 常に丁寧で簡潔な日本語で応答してください。箇条書きは使いすぎず、会話らしい文章を心がけて
  ください。
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
]

TOOL_REGISTRY = {
    "search_akiya": tools.search_akiya,
    "estimate_renovation_cost": tools.estimate_renovation_cost,
    "simulate_income": tools.simulate_income,
    "search_subsidies": tools.search_subsidies,
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
        """ユーザーの発話を送り、必要なツール呼び出しを内部で完結させた上で最終回答を返す。"""
        response = self.chat.send_message(user_message)
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
            response = self.chat.send_message(function_response_parts)

        reply = response.text or "(応答を生成できませんでした。もう一度お試しください)"
        return AgentTurnResult(reply=reply, tool_calls=tool_calls_log)


if __name__ == "__main__":
    # 簡易動作確認。GEMINI_API_KEY が必要。
    agent = AkiyaAgent()
    result = agent.send("千葉県でカフェを開きたいです。予算は300万円くらいです。")
    print(result.reply)
    for tc in result.tool_calls:
        print(f"[tool] {tc.name}({tc.args}) -> {tc.result}")
