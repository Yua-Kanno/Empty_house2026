"""
空き家AIプランナー - 提案書PDF生成(C担当)

houses.json (B担当のデータ) を読み込み、指定した物件1件分の
提案書PDFを生成する最小構成スクリプト。

使い方:
    python generate_proposal.py --id 1                 # id=1の物件を1件だけ出力
    python generate_proposal.py --all                   # houses.json全件を出力
    python generate_proposal.py --id 1 --input other.json --output-dir out/
"""

import argparse
import json
import os
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
)

# ---------- 日本語フォント登録 ----------
# reportlab組み込みのCIDフォント(HeiseiKakuGo-W5等)はフォントを埋め込まず
# 「閲覧環境に日本語フォントがある」前提になり、環境によっては文字が表示されない
# (枠線だけ出る)ことがあるため、TTFを直接埋め込む方式にする。
# フォントファイルは japanize-matplotlib パッケージに同梱されているIPAexゴシックを使う
# (`pip install japanize-matplotlib` が必要。OSに依存せず確実に動くため採用)。
import japanize_matplotlib

_FONT_PATH = os.path.join(os.path.dirname(japanize_matplotlib.__file__), "fonts", "ipaexg.ttf")
pdfmetrics.registerFont(TTFont("NotoSansJP", _FONT_PATH))       # 本文用ゴシック
pdfmetrics.registerFont(TTFont("NotoSansJP-Bold", _FONT_PATH))  # 見出し用(太字ウェイトが無いため同フォントを流用)

# ---------- 配色 (地図・グラフ画面と統一) ----------
INK = colors.HexColor("#22344B")
INK_SOFT = colors.HexColor("#4C5E73")
LINE = colors.HexColor("#D9D0BC")
PANEL = colors.HexColor("#FBF9F4")
MOSS = colors.HexColor("#5F7350")
RUST = colors.HexColor("#954C36")
GOLD = colors.HexColor("#B0862A")

FACILITY_LABEL = {
    "shower": "シャワー", "toilet": "トイレ", "bath": "浴室", "electricity": "電気",
    "water": "水道", "sewage": "下水", "propane_gas": "プロパンガス", "septic_tank": "浄化槽",
}


def load_properties(path: str) -> list[dict]:
    """B担当のhouses.json(自治体ごとにネストした構造)を、フラットな配列に展開して読み込む。"""
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)

    properties = []
    for group in raw:
        municipality = group["search_condition"]["municipality"]
        for p in group["properties"]:
            item = dict(p)
            item["uid"] = f'{municipality}-{p["id"]}'
            properties.append(item)
    return properties


def yen(value) -> str:
    if value is None:
        return "問い合わせ"
    return f"{value:,}円"


def build_styles() -> dict:
    return {
        "title": ParagraphStyle(
            "title", fontName="NotoSansJP-Bold", fontSize=18, leading=24, textColor=INK,
        ),
        "address": ParagraphStyle(
            "address", fontName="NotoSansJP", fontSize=10, leading=14, textColor=INK_SOFT,
        ),
        "section": ParagraphStyle(
            "section", fontName="NotoSansJP-Bold", fontSize=12, leading=16, textColor=INK,
            spaceBefore=14, spaceAfter=6,
        ),
        "body": ParagraphStyle(
            "body", fontName="NotoSansJP", fontSize=10, leading=16, textColor=INK,
        ),
        "note": ParagraphStyle(
            "note", fontName="NotoSansJP", fontSize=9, leading=13, textColor=INK_SOFT,
        ),
    }


def build_info_table(house: dict) -> Table:
    rows = [
        ["価格", yen(house.get("price")), "改修費概算", yen(house.get("renovation_cost_est"))],
        ["間取り", house.get("layout") or "不明", "構造", house.get("structure") or "不明"],
        ["土地面積", f'{house["land_area_m2"]}㎡' if house.get("land_area_m2") else "-",
         "建物面積", f'{house["building_area_m2"]}㎡' if house.get("building_area_m2") else "-"],
    ]
    table = Table(rows, colWidths=[28 * mm, 55 * mm, 28 * mm, 55 * mm])
    table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), "NotoSansJP"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("TEXTCOLOR", (0, 0), (0, -1), INK_SOFT),
        ("TEXTCOLOR", (2, 0), (2, -1), INK_SOFT),
        ("TEXTCOLOR", (1, 0), (1, -1), INK),
        ("TEXTCOLOR", (3, 0), (3, -1), INK),
        ("LINEBELOW", (0, 0), (-1, -2), 0.5, LINE),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return table


def build_cost_summary(house: dict) -> Table:
    price = house.get("price")
    reno = house.get("renovation_cost_est")
    # 片方でもnull(不明)なら合計も算出できないため、素直に「問い合わせ」扱いにする
    total = None if price is None or reno is None else price + reno

    rows = [["物件価格", "改修費概算", "総額(概算)"], [yen(price), yen(reno), yen(total)]]
    table = Table(rows, colWidths=[47 * mm, 47 * mm, 47 * mm])
    table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), "NotoSansJP"),
        ("FONTNAME", (0, 1), (-1, 1), "NotoSansJP-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 9),
        ("FONTSIZE", (0, 1), (-1, 1), 12),
        ("TEXTCOLOR", (0, 0), (-1, 0), INK_SOFT),
        ("TEXTCOLOR", (0, 1), (0, 1), INK_SOFT),
        ("TEXTCOLOR", (1, 1), (1, 1), RUST),
        ("TEXTCOLOR", (2, 1), (2, 1), GOLD),
        ("BACKGROUND", (0, 0), (-1, -1), PANEL),
        ("BOX", (0, 0), (-1, -1), 0.5, LINE),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, LINE),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    return table


def build_facilities_line(house: dict) -> str:
    facilities = house.get("facilities") or {}
    available = [FACILITY_LABEL[k] for k, v in facilities.items() if v and k in FACILITY_LABEL]
    return "、".join(available) if available else "情報なし"


def build_subsidy_section(styles: dict, house: dict) -> list:
    """house['subsidies']が渡されていればそれを表示し、無ければ準備中の文言を出す。"""
    subsidies = house.get("subsidies")
    section = [Paragraph("該当する補助金・支援制度", styles["section"])]

    if not subsidies:
        section.append(Paragraph(
            "補助金データは現在準備中です。反映され次第、対象となる制度をここに表示します。",
            styles["note"],
        ))
        return section

    for s in subsidies:
        name = s.get("subsidy_name") or s.get("name") or "-"
        max_amount = s.get("max_amount")
        amount_text = f'上限{int(max_amount):,}円' if max_amount not in (None, "") else "上限不明"
        conditions = s.get("conditions") or s.get("description") or ""
        section.append(Paragraph(f"<b>{name}</b>({amount_text})", styles["body"]))
        if conditions:
            section.append(Paragraph(conditions, styles["note"]))
        section.append(Spacer(1, 4))

    return section


def build_proposal(house: dict, output_path: str | None):
    """提案書PDFを作る。
    - output_path が文字列(パス)の場合: そのパスにPDFを保存し、Noneを返す(CLI用)。
    - output_path が None の場合: ファイルに保存せず、メモリ上のBytesIOを返す(Flask等から呼ぶ用)。
    """
    is_in_memory = output_path is None
    target = BytesIO() if is_in_memory else output_path

    styles = build_styles()
    doc = SimpleDocTemplate(
        target, pagesize=A4,
        topMargin=20 * mm, bottomMargin=20 * mm, leftMargin=20 * mm, rightMargin=20 * mm,
    )

    story = []

    story.append(Paragraph("空き家活用プラン提案書", styles["note"]))
    story.append(Spacer(1, 4))
    story.append(Paragraph(house["title"], styles["title"]))
    story.append(Paragraph(house.get("address", ""), styles["address"]))
    story.append(Spacer(1, 10))
    story.append(HRFlowable(width="100%", thickness=0.75, color=LINE))

    story.append(Paragraph("物件概要", styles["section"]))
    story.append(build_info_table(house))

    story.append(Paragraph("設備", styles["section"]))
    story.append(Paragraph(build_facilities_line(house), styles["body"]))

    story.append(Paragraph("特徴", styles["section"]))
    story.append(Paragraph(house.get("features") or "-", styles["body"]))

    story.append(Paragraph("費用シミュレーション", styles["section"]))
    story.append(build_cost_summary(house))

    story.extend(build_subsidy_section(styles, house))

    doc.build(story)

    if is_in_memory:
        target.seek(0)
        return target
    return None


def main():
    parser = argparse.ArgumentParser(description="空き家提案書PDFを生成する")
    parser.add_argument("--input", default="houses.json", help="読み込むJSONファイル")
    parser.add_argument("--output-dir", default="output", help="PDFの出力先フォルダ")
    parser.add_argument("--id", type=int, help="出力する物件のid(houses.json内のid)")
    parser.add_argument("--all", action="store_true", help="全件出力する")
    args = parser.parse_args()

    properties = load_properties(args.input)
    os.makedirs(args.output_dir, exist_ok=True)

    if args.all:
        targets = properties
    elif args.id is not None:
        targets = [p for p in properties if p["id"] == args.id]
        if not targets:
            raise SystemExit(f"id={args.id} の物件が見つかりませんでした")
    else:
        targets = properties[:1]  # 指定なしなら先頭1件をサンプル出力

    for house in targets:
        output_path = os.path.join(args.output_dir, f'proposal_{house["uid"]}.pdf')
        build_proposal(house, output_path)
        print(f"生成しました: {output_path}")


if __name__ == "__main__":
    main()