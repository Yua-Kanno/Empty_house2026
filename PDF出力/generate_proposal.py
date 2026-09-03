"""
空き家AIプランナー - 提案書PDF生成(C担当)

houses.json (B担当のデータ) を読み込み、指定した物件1件分の
提案書PDFを生成するスクリプト。
"""

import argparse
import json
import os
import sys
import urllib.request
import zipfile
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

# ---------- 日本語フォント自動検出・自動ダウンロード・登録 ----------
def setup_japanese_font():
    current_dir = os.path.dirname(os.path.abspath(__file__))
    target_ttf = os.path.join(current_dir, "ipaexg.ttf")

    # 1. 既に ipaexg.ttf がないかチェック
    if not os.path.exists(target_ttf) or os.path.getsize(target_ttf) < 1000:
        print("⏳ 日本語フォント(ipaexg.ttf)が見つかりません。自動ダウンロード中...")
        zip_path = os.path.join(current_dir, "ipaexg00401.zip")
        url = "https://i3s.opencontent.jp/ipaexg/ipaexg00401.zip"
        
        try:
            # 公式からZIPをダウンロード
            urllib.request.urlretrieve(url, zip_path)
            # 解凍して ttf ファイルを配置
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                for file in zip_ref.namelist():
                    if file.endswith("ipaexg.ttf"):
                        zip_ref.extract(file, current_dir)
                        extracted_file = os.path.join(current_dir, file)
                        os.rename(extracted_file, target_ttf)
                        # 解凍後の空フォルダを削除
                        folder_path = os.path.dirname(extracted_file)
                        if os.path.exists(folder_path) and folder_path != current_dir:
                            try:
                                os.rmdir(folder_path)
                            except OSError:
                                pass
                        break
            if os.path.exists(zip_path):
                os.remove(zip_path)
            print("✨ フォントの自動ダウンロードと配置が完了しました！")
        except Exception as e:
            print(f"⚠️ フォントの自動ダウンロードに失敗しました: {e}")

    # 2. ロード可能か検証して登録
    selected_font = target_ttf if os.path.exists(target_ttf) else None
    
    if selected_font:
        try:
            pdfmetrics.registerFont(TTFont("NotoSansJP", selected_font))
            pdfmetrics.registerFont(TTFont("NotoSansJP-Bold", selected_font))
            print(f"✅ 使用フォント: {selected_font}")
            return
        except Exception as e:
            print(f"⚠️ 登録失敗: {e}")

    raise FileNotFoundError(
        "日本語フォントの準備に失敗しました。ネットワーク接続を確認して再実行してください。"
    )

setup_japanese_font()

# ---------- 配色定義 ----------
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
    if not os.path.exists(path):
        # データが無い場合のダミーフォールバック
        return [{
            "id": 1,
            "uid": "demo-1",
            "title": "【サンプル】長野県〇〇市 伝統的木造家屋",
            "address": "長野県〇〇市大字123",
            "price": 5000000,
            "renovation_cost_est": 8500000,
            "layout": "5LDK",
            "structure": "木造2階建",
            "land_area_m2": 250.0,
            "building_area_m2": 120.0,
            "facilities": {"shower": True, "toilet": True, "water": True},
            "features": "日当たり良好、梁が見える伝統的な構造。カフェやワークスペースに最適。"
        }]

    with open(path, encoding="utf-8") as f:
        raw = json.load(f)

    properties = []
    for group in raw:
        municipality = group.get("search_condition", {}).get("municipality", "area")
        for p in group.get("properties", []):
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
        "title": ParagraphStyle("title", fontName="NotoSansJP-Bold", fontSize=18, leading=24, textColor=INK),
        "address": ParagraphStyle("address", fontName="NotoSansJP", fontSize=10, leading=14, textColor=INK_SOFT),
        "section": ParagraphStyle("section", fontName="NotoSansJP-Bold", fontSize=12, leading=16, textColor=INK, spaceBefore=14, spaceAfter=6),
        "body": ParagraphStyle("body", fontName="NotoSansJP", fontSize=10, leading=16, textColor=INK),
        "note": ParagraphStyle("note", fontName="NotoSansJP", fontSize=9, leading=13, textColor=INK_SOFT),
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

def build_subsidy_table(house: dict, styles: dict):
    subsidies = house.get("subsidies") or []
    if not subsidies:
        return Paragraph("該当する補助金情報はありません。", styles["note"])

    rows = [[
        Paragraph("制度名", styles["note"]),
        Paragraph("上限・補助率", styles["note"]),
        Paragraph("主な条件", styles["note"]),
    ]]
    for subsidy in subsidies:
        rows.append([
            Paragraph(subsidy.get("subsidy_name") or "-", styles["body"]),
            Paragraph(
                f'{yen(subsidy.get("max_amount"))}<br/>{subsidy.get("rate") or "-"}',
                styles["body"],
            ),
            Paragraph(subsidy.get("conditions") or "-", styles["body"]),
        ])

    table = Table(rows, colWidths=[65 * mm, 38 * mm, 65 * mm], repeatRows=1)
    table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), "NotoSansJP"),
        ("BACKGROUND", (0, 0), (-1, 0), PANEL),
        ("BOX", (0, 0), (-1, -1), 0.5, LINE),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return table

def build_proposal(house: dict, output_path: str = None):
    """
    提案書PDFを生成する。
    output_path が None の場合、BytesIO オブジェクトを返す。
    output_path が指定された場合、ファイルに保存して None を返す。
    """
    styles = build_styles()
    
    # output_path が None の場合はメモリ上に生成
    if output_path is None:
        pdf_buffer = BytesIO()
        doc = SimpleDocTemplate(
            pdf_buffer, pagesize=A4,
            topMargin=20 * mm, bottomMargin=20 * mm, leftMargin=20 * mm, rightMargin=20 * mm,
        )
    else:
        doc = SimpleDocTemplate(
            output_path, pagesize=A4,
            topMargin=20 * mm, bottomMargin=20 * mm, leftMargin=20 * mm, rightMargin=20 * mm,
        )

    story = [
        Paragraph("空き家活用プラン提案書", styles["note"]),
        Spacer(1, 4),
        Paragraph(house.get("title", "空き家物件提案"), styles["title"]),
        Paragraph(house.get("address", ""), styles["address"]),
        Spacer(1, 10),
        HRFlowable(width="100%", thickness=0.75, color=LINE),
        Paragraph("物件概要", styles["section"]),
        build_info_table(house),
        Paragraph("設備", styles["section"]),
        Paragraph(build_facilities_line(house), styles["body"]),
        Paragraph("特徴", styles["section"]),
        Paragraph(house.get("features") or "-", styles["body"]),
        Paragraph("費用シミュレーション", styles["section"]),
        build_cost_summary(house),
        Paragraph("該当する補助金・支援制度", styles["section"]),
        build_subsidy_table(house, styles)
    ]

    doc.build(story)
    
    if output_path is None:
        pdf_buffer.seek(0)
        return pdf_buffer
    return None

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="空き家提案書PDFを生成する")
    parser.add_argument("--input", default="houses.json", help="読み込むJSONファイル")
    parser.add_argument("--output-dir", default="output", help="PDFの出力先フォルダ")
    parser.add_argument("--id", type=int, help="出力する物件のid")
    args = parser.parse_args()

    properties = load_properties(args.input)
    os.makedirs(args.output_dir, exist_ok=True)

    target = next((p for p in properties if p["id"] == args.id), properties[0])
    output_path = os.path.join(args.output_dir, f'proposal_{target["uid"]}.pdf')
    build_proposal(target, output_path)
    print(f"生成完了: {output_path}")