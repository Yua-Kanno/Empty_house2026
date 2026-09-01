# 空き家AIプランナー — エージェント/チャットUI (A担当分)

VORN Challenge 2026「空き家AIプランナー」のうち、A担当(エージェント設計・チャットUI)のコードです。
Gemini API の function calling を使い、ユーザーとの対話の中で「空き家検索 → 改修コスト概算 →
収支シミュレーション → 補助金検索」を自律的につないで提案するエージェントと、動作確認用の
CLI / Web チャットUIが入っています。

## 1. セットアップ

```bash
pip install -r requirements.txt
cp .env.example .env
# .env を開いて GEMINI_API_KEY にAPIキーを設定
# (Google AI Studio https://aistudio.google.com/apikey で無料取得可能)
```

### CLIで試す

```bash
python3 chat_cli.py
```

`/tools` で直前のツール呼び出しログ、`/reset` で会話リセット、`/quit` で終了できます。

### Webチャットで試す

```bash
uvicorn web.app:app --reload --port 8000
```

ブラウザで `http://localhost:8000` を開くとチャット画面が表示されます。

## 2. ディレクトリ構成

```
akiya-ai-planner/
├── agent.py            # エージェント本体(システムプロンプト・function calling制御)
├── tools.py             # 4つのツール実装(空き家検索/改修コスト/収支/補助金検索)
├── chat_cli.py          # CLI版チャット
├── data/
│   ├── akiya_sample.json      # 空き家サンプルデータ(ダミー)
│   └── subsidies_sample.json  # 補助金サンプルデータ(ダミー)
├── web/
│   ├── app.py            # FastAPI backend (POST /api/chat 等)
│   └── static/index.html # チャットUI(素のHTML/CSS/JS)
├── requirements.txt
└── .env.example
```

## 3. アーキテクチャ

- **agent.py** — `AkiyaAgent` クラスが1ユーザー分の会話状態を保持します。`send(message)` を呼ぶと、
  Geminiが必要に応じて `tools.py` の関数をfunction callingで呼び出し、その結果を踏まえた最終回答を
  `AgentTurnResult(reply, tool_calls)` として返します。`tool_calls` にはそのターンで実行された
  ツール名・引数・生の結果がすべて入っており、UI側で地図やグラフの描画に再利用できます。
- **tools.py** — 4つのツールはすべて `data/*.json` のモックデータに対する検索・簡易計算のみで、
  外部DBやMLモデルには依存していません。**引数名・返り値の形を変えなければ中身は自由に差し替え可能**
  な設計にしています(下記「B担当への引き継ぎ」参照)。
- **チャットUI** — CLI版とWeb版の2つを用意しています。Web版はバックエンド(FastAPI)とフロント
  (素のHTML/JS)がシンプルな `POST /api/chat` 1本で繋がっているだけなので、C担当が地図・グラフを
  組み込む際もこのAPIをそのまま叩けば必要なデータが揃います。

### `POST /api/chat` の仕様

リクエスト:
```json
{ "session_id": "会話を継続する場合は前回のレスポンスのsession_idを渡す。初回はnullでよい", "message": "ユーザーの発話" }
```

レスポンス:
```json
{
  "session_id": "...",
  "reply": "エージェントの返答テキスト",
  "tool_calls": [
    { "name": "search_akiya", "args": {"area": "千曲市", "...": "..."}, "result": { "count": 4, "results": [ {"id": "...", "lat": 36.5, "lng": 138.1, "price_man_yen": 150, "...": "..."} ] } },
    { "name": "estimate_renovation_cost", "args": {...}, "result": {...} },
    { "name": "simulate_income", "args": {...}, "result": {...} },
    { "name": "search_subsidies", "args": {...}, "result": {...} }
  ]
}
```

## 4. B担当への引き継ぎポイント(DB・データ)

`tools.py` の4関数は、現状すべて `data/*.json` を読むだけの実装です。実データ・実DBに繋ぎ込む際は、
**関数シグネチャ(引数名・返り値のキー)を変えずに関数の中身だけ差し替えれば** `agent.py` 側は
無改修で動きます。

- `search_akiya(...)` → PostgreSQLへのクエリに差し替え。返り値は `{"count": int, "results": [物件dict...]}`
  の形を維持してください(`lat`/`lng` は地図表示に必須、`price_man_yen`・`building_area_sqm`・
  `built_year`・`structure` は改修コスト概算にそのまま渡されます)。
- `search_subsidies(...)` → 補助金データが増えたら `data/subsidies_sample.json` に追記するだけでも
  動きます。本格的なRAGにする場合は `_try_embedding_search` 内のロジックをベクトルDB検索に差し替えて
  ください(`GEMINI_API_KEY` があれば現状もGemini Embeddingsでの簡易セマンティック検索が動きます。
  未設定時はキーワード検索にフォールバックします)。
- `estimate_renovation_cost(...)` は現状シンプルな数式です。scikit-learnの回帰モデルに差し替える際は、
  返り値に `estimated_cost_man_yen` / `cost_range_man_yen` / `breakdown` / `note` を含める形を
  維持してもらえると、UI側の表示コードを変えずに済みます。

## 5. C担当への引き継ぎポイント(地図・グラフ・PDF)

- **地図**: `tool_calls` から `name == "search_akiya"` の `result.results` を取り出すと、各物件に
  `lat` / `lng` が入っているのでLeaflet.jsにそのまま渡せます。
- **グラフ**: `name == "simulate_income"` の `result` に `monthly_revenue_man_yen` /
  `monthly_cost_man_yen` / `monthly_profit_man_yen` / `payback_period_years` が入っています。
  Chart.jsの棒グラフ・積み上げグラフの入力としてそのまま使えます。
- **PDF提案書**: 会話の最後にエージェントがまとめる提案サマリー(`reply`のテキスト)に加えて、
  同じターンの `tool_calls` を保存しておけば、選ばれた物件・改修コスト・収支・補助金の構造化データを
  そのままReportLabのテンプレートに流し込めます。「どの物件が最終的に選ばれたか」をUI側で
  クリック等により明示的にマークする仕組みは未実装なので、必要であれば追加してください。
- 現状のWeb版UI(`web/static/index.html`)はツール結果を簡易カード表示しているだけなので、
  地図・グラフの実装時は自由に置き換え/拡張してもらって構いません。

## 6. 既知の制限事項・今後のTODO

- **モックデータ**: `data/akiya_sample.json`・`data/subsidies_sample.json` はどちらもダミー
  データです(千曲市・尾道市の物件12件、補助金12件)。実データに差し替えるまでは、金額・条件を
  実在の制度として案内しないよう注意してください。
- **セッション管理**: Web版は `web/app.py` の `SESSIONS` にインメモリで会話を保持しているだけです。
  プロセスを再起動すると会話は消えます。複数人でのデモや永続化が必要になったらRedis等への
  差し替えを検討してください。
- **LLM切り替え**: 現状は Gemini API 専用実装です(`google-genai` SDK、function calling)。
  もしOpenAI/Claude APIへの切り替えが必要になった場合は、`agent.py` の `FUNCTION_DECLARATIONS`
  (JSON Schemaなのでほぼそのまま流用可能)と `AkiyaAgent` クラス内のAPI呼び出し部分のみを
  差し替えれば、`tools.py` は無改修で使えます。
- **改修コスト・収支モデル**: どちらも簡易的な数式によるプレースホルダーです。ピッチでは
  「簡易モデルによる概算」であることを明示してください。
