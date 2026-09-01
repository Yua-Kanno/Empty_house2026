"""
chat_cli.py
===========
ターミナルでエージェントと対話するための簡易CLI。
エージェントの対話ロジック(agent.py)だけを素早く確認したいときに使う。

使い方:
    export GEMINI_API_KEY=xxxxx   # または .env ファイルに記載
    python3 chat_cli.py

コマンド:
    /tools   直前のターンで呼び出されたツールとその結果を表示
    /reset   会話をリセットして最初からやり直す
    /quit    終了
"""

from __future__ import annotations

import sys

from agent import AkiyaAgent


def main() -> None:
    print("=== 空き家AIプランナー (CLI版) ===")
    print("終了するには /quit と入力してください。ツール呼び出しログは /tools で確認できます。\n")

    try:
        agent = AkiyaAgent()
    except RuntimeError as e:
        print(f"起動エラー: {e}")
        sys.exit(1)

    last_tool_calls = []

    print("エージェント: こんにちは。移住や空き家活用について、気になっていることを教えてください。")

    while True:
        try:
            user_input = input("\nあなた: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n終了します。")
            break

        if not user_input:
            continue
        if user_input in ("/quit", "/exit"):
            print("終了します。")
            break
        if user_input == "/tools":
            if not last_tool_calls:
                print("(直前のターンでツール呼び出しはありませんでした)")
            for tc in last_tool_calls:
                print(f"- {tc.name}({tc.args})")
                print(f"  -> {tc.result}")
            continue
        if user_input == "/reset":
            agent = AkiyaAgent()
            last_tool_calls = []
            print("会話をリセットしました。")
            continue

        result = agent.send(user_input)
        last_tool_calls = result.tool_calls
        if result.tool_calls:
            tool_names = ", ".join(tc.name for tc in result.tool_calls)
            print(f"  [ツール呼び出し: {tool_names}]")
        print(f"\nエージェント: {result.reply}")


if __name__ == "__main__":
    main()
