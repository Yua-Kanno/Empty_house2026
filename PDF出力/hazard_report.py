"""
空き家AIプランナー - 防災レポート(ハザード)PDF生成

選んだ物件1件について、次の内容を1〜2ページのPDFにまとめる。
  1. 物件の情報
  2. ハザード4種(洪水・土砂災害・津波・高潮)の簡易判定と、物件周辺の地図
  3. 備えておきたいもののチェックリスト
  4. 注意書きと出典

判定の方法は、画面(フロント/script.js の updateHazardInfo)と同じ:
  国土交通省「ハザードマップポータルサイト」のタイル画像のうち、物件の地点のピクセルに
  色がついているか(透明でないか)を調べる。タイルが無い(404)ときは「指定区域外の可能性」。
地図の下絵は、国土地理院の淡色地図(出典: 地理院タイル)。

あくまで参考情報で、安全を保証するものではない。PDFの中にもそう明記している。
"""
from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from io import BytesIO
from typing import Callable, Optional

import requests
from PIL import Image, ImageDraw
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable,
    Image as RLImage, PageBreak, CondPageBreak,
)
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle

import generate_proposal as gp
import shelters as sh  # フォント登録・配色・基本スタイルを提案書と共通にする

HAZARD_BASE = "https://disaportaldata.gsi.go.jp/raster/"
BASE_MAP_URL = "https://cyberjapandata.gsi.go.jp/xyz/pale/{z}/{x}/{y}.png"
JUDGE_ZOOM = 16        # 判定に使うズーム(画面側の HAZARD_PIXEL_CHECK_ZOOM と同じ)
MAP_ZOOM = 15          # 地図画像のズーム
MAP_SIZE = (520, 340)  # 地図画像の大きさ(px)
OVERLAY_OPACITY = 0.7
USER_AGENT = "akiya-ai-planner-hazard-report/1.0 (student project)"

# 画面(フロント/script.js の HAZARD_LAYERS)と同じ内容
HAZARDS = [
    {"key": "flood", "label": "洪水浸水想定区域", "layers": ["01_flood_l2_shinsuishin_data"],
     "note": "想定し得る最大規模の降雨で、河川があふれた場合の浸水想定"},
    {"key": "sediment", "label": "土砂災害警戒区域", "layers": ["05_dosekiryukeikaikuiki", "05_kyukeishakeikaikuiki"],
     "note": "土石流・急傾斜地の崩壊のおそれがある区域"},
    {"key": "tsunami", "label": "津波浸水想定", "layers": ["04_tsunami_newlegend_data"],
     "note": "津波による浸水が想定される区域"},
    {"key": "hightide", "label": "高潮浸水想定区域", "layers": ["03_hightide_l2_shinsuishin_data"],
     "note": "高潮による浸水が想定される区域"},
]

# 備えておきたいもの(首相官邸・内閣府・各市区町村の防災の案内にある一般的な例をもとにした目安)
CHECKLIST = [
    ("みんな共通(備蓄は最低3日分、できれば1週間分が目安)", [
        "飲料水(1人1日3リットルが目安)",
        "食料(アルファ米・缶詰・レトルトなど)",
        "携帯トイレ(1人1日5回分が目安)",
        "懐中電灯・携帯ラジオ(手動充電式が便利)",
        "予備の電池・モバイルバッテリー",
        "救急用品・常備薬",
        "現金・身分証・保険証(のコピー)",
        "マスク・ティッシュなどの衛生用品",
        "雨具・防寒具・タオル",
        "履き慣れた底の厚い靴・軍手",
    ]),
    ("水害・高潮のおそれがあるとき", [
        "ビニール袋(スマホ・貴重品を水から守る)",
        "レインウェア(両手があく雨具)",
    ]),
    ("地震・津波のおそれがあるとき", [
        "ヘルメット・防災ずきん",
        "ホイッスル(閉じ込められたときの合図)",
        "枕元のスリッパ・懐中電灯",
    ]),
    ("土砂災害のおそれがあるとき", [
        "持ち出し袋を玄関に(雨が強まる前に、早めに避難できるように)",
    ]),
]

TileFetcher = Callable[[str], "tuple[str, Optional[Image.Image]]"]


# ---------- タイルの取得 ----------
def default_fetch_tile(url: str):
    """(状態, 画像) を返す。状態は 'ok' / 'missing'(404=その区域の指定なし) / 'error'。"""
    try:
        res = requests.get(url, timeout=8, headers={"User-Agent": USER_AGENT})
    except requests.RequestException:
        return "error", None
    if res.status_code == 404:
        return "missing", None
    if res.status_code != 200:
        return "error", None
    try:
        img = Image.open(BytesIO(res.content)).convert("RGBA")
    except Exception:  # 画像として読めない(JSONのエラー応答など)
        return "missing", None
    return "ok", img


def lnglat_to_tile_float(lat: float, lng: float, zoom: int):
    n = 2 ** zoom
    x = (lng + 180.0) / 360.0 * n
    lat_rad = math.radians(lat)
    y = (1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * n
    return x, y


# ---------- 判定(画面側と同じ方法)----------
def judge_hazard(lat: float, lng: float, hazard: dict, fetch: TileFetcher) -> str:
    """'risk' / 'no-data' / 'error' を返す。"""
    xf, yf = lnglat_to_tile_float(lat, lng, JUDGE_ZOOM)
    tx, ty = int(math.floor(xf)), int(math.floor(yf))
    px, py = int((xf - tx) * 256), int((yf - ty) * 256)
    saw_error = False
    for layer in hazard["layers"]:
        url = f"{HAZARD_BASE}{layer}/{JUDGE_ZOOM}/{tx}/{ty}.png"
        state, img = fetch(url)
        if state == "error":
            saw_error = True
            continue
        if state == "ok" and img is not None:
            w, h = img.size
            alpha = img.getpixel((min(px * w // 256, w - 1), min(py * h // 256, h - 1)))[3]
            if alpha > 10:
                return "risk"
    return "error" if saw_error else "no-data"


# ---------- 地図画像 ----------
def _stitch(tiles: dict, x0: int, y0: int, nx: int, ny: int, mode: str = "RGBA") -> Image.Image:
    canvas = Image.new(mode, (256 * nx, 256 * ny), (255, 255, 255, 0) if mode == "RGBA" else (238, 238, 238))
    for (tx, ty), img in tiles.items():
        if img is not None:
            canvas.paste(img.resize((256, 256)), ((tx - x0) * 256, (ty - y0) * 256))
    return canvas


def render_hazard_map(lat: float, lng: float, hazard: dict, fetch: TileFetcher, base_cache: dict) -> Optional[Image.Image]:
    """淡色地図の上に、そのハザードの色を重ね、物件の位置にしるしを付けた画像を返す。下絵が取れなければNone。"""
    z = MAP_ZOOM
    xf, yf = lnglat_to_tile_float(lat, lng, z)
    cx, cy = int(math.floor(xf)), int(math.floor(yf))
    x0, y0, nx, ny = cx - 1, cy - 1, 3, 3
    coords = [(x0 + i, y0 + j) for j in range(ny) for i in range(nx)]

    def get(url):
        return fetch(url)

    # 下絵(全ハザードで共通なので、1回だけ取得して使い回す)
    if "base" not in base_cache:
        results = [get(BASE_MAP_URL.format(z=z, x=x, y=y)) for x, y in coords]
        got = {c: r[1] for c, r in zip(coords, results)}
        base_cache["base"] = None if all(v is None for v in got.values()) else _stitch(got, x0, y0, nx, ny, "RGB")
    base = base_cache["base"]
    if base is None:
        return None
    base = base.convert("L").convert("RGB")                       # 白黒にして、色を見やすくする
    base = Image.blend(base, Image.new("RGB", base.size, (255, 255, 255)), 0.25)

    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    for layer in hazard["layers"]:
        got = {}
        for x, y in coords:
            state, img = get(f"{HAZARD_BASE}{layer}/{z}/{x}/{y}.png")
            got[(x, y)] = img if state == "ok" else None
        part = _stitch(got, x0, y0, nx, ny, "RGBA")
        overlay = Image.alpha_composite(overlay, part)
    r, g, b, a = overlay.split()
    overlay.putalpha(a.point(lambda v: int(v * OVERLAY_OPACITY)))
    composed = Image.alpha_composite(base.convert("RGBA"), overlay).convert("RGB")

    # 物件の位置のしるし
    mx, my = (xf - x0) * 256, (yf - y0) * 256
    d = ImageDraw.Draw(composed)
    d.ellipse([mx - 11, my - 11, mx + 11, my + 11], fill=(255, 255, 255))
    d.ellipse([mx - 8, my - 8, mx + 8, my + 8], fill=(200, 30, 30))
    d.ellipse([mx - 3, my - 3, mx + 3, my + 3], fill=(255, 255, 255))

    w, h = MAP_SIZE
    left, top = int(mx - w / 2), int(my - h / 2)
    left = max(0, min(left, composed.width - w))
    top = max(0, min(top, composed.height - h))
    return composed.crop((left, top, left + w, top + h))


def _cached(fetch: TileFetcher) -> TileFetcher:
    cache: dict = {}
    def inner(url):
        if url not in cache:
            cache[url] = fetch(url)
        return cache[url]
    return inner


# ---------- PDF ----------
VERDICT_TEXT = {
    "risk": "区域に含まれる可能性があります",
    "no-data": "指定区域外の可能性があります",
    "error": "確認できませんでした(通信エラー)",
}
VERDICT_COLOR = {"risk": gp.RUST, "no-data": gp.MOSS, "error": gp.INK_SOFT}


def _p(text, style):
    return Paragraph(text, style)


def _shelter_table(rows, header, colw, st, small):
    data = [[_p(f"<b>{t}</b>", small) for t in header]] + rows
    t = Table(data, colWidths=colw, repeatRows=1)
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), gp.PANEL), ("GRID", (0, 0), (-1, -1), 0.3, gp.LINE),
                           ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 3),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 3), ("LEFTPADDING", (0, 0), (-1, -1), 4),
                           ("RIGHTPADDING", (0, 0), (-1, -1), 4)]))
    return t


def _shelter_section(lat, lng, need, st, small, warn):
    """近くの避難場所・避難所。データがない/読めないときは、確認を促す案内だけを出す。"""
    generic = ("避難する場所は、災害の種類によって異なります。洪水のときに浸水する場所は、洪水の避難先に使えません。"
               "市区町村が公表している避難場所と避難所を、ハザードマップポータルサイトや、市区町村の公式ハザードマップで、現地に行く前に確認してください。")
    out = []
    try:
        emergency = sh.nearest(lat, lng, "emergency", limit=5, need=need) if sh.has_data() else []
        designated = sh.nearest(lat, lng, "designated", limit=4) if sh.has_data() else []
    except Exception:
        emergency, designated = [], []

    if not emergency and not designated:
        box = Table([[_p("<b>近くの避難場所</b><br/>" + generic, warn)]], colWidths=[178 * mm])
        box.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.5, gp.LINE), ("TOPPADDING", (0, 0), (-1, -1), 6),
                                 ("BOTTOMPADDING", (0, 0), (-1, -1), 6), ("LEFTPADDING", (0, 0), (-1, -1), 8)]))
        return [box]

    def dist(x):
        return f'{x["distance_m"]}m<br/>(徒歩 約{x["walk_min"]}分)'

    out += [_p("近くの避難場所・避難所", st["section"])]
    if emergency:
        out += [_p("<b>指定緊急避難場所</b>(災害から命を守るために、いったん逃げる場所)", st["body"])]
        if need:
            names = "・".join(sh.FLAG_LABELS[k] for k in need)
            out += [_p(f"この物件は「{names}」に含まれる可能性があるため、それに対応する避難場所を先に並べています。"
                       "対応していないものには「※」を付けています。", small)]
        rows = []
        for x in emergency:
            mark = "" if x.get("matches_need", True) else "※"
            rows.append([_p(f'{mark}{x["name"]}', small), _p(x["address"], small), _p(dist(x), small),
                         _p("・".join(x["supports"]) or "(記載なし)", small)])
        out += [_shelter_table(rows, ["名称", "住所", "距離", "対応する災害"], [48 * mm, 62 * mm, 24 * mm, 44 * mm], st, small),
                Spacer(1, 3 * mm)]
    if designated:
        out += [_p("<b>指定避難所</b>(自宅に戻れないとき、一定期間、避難生活を送る場所)", st["body"])]
        rows = [[_p(x["name"], small), _p(x["address"], small), _p(dist(x), small),
                 _p(x["target"] or "(記載なし)", small)] for x in designated]
        out += [_shelter_table(rows, ["名称", "住所", "距離", "受け入れ対象者"], [48 * mm, 62 * mm, 24 * mm, 44 * mm], st, small),
                Spacer(1, 3 * mm)]
    out += [_p("距離は物件の位置からの直線距離で、実際の道のりはもっと長くなります。徒歩の目安は1分あたり80mで計算しています。" + generic, small),
            _p(sh.ATTRIBUTION, small)]
    return out


def build_hazard_report(house: dict, output_path: Optional[str] = None, fetch: TileFetcher = default_fetch_tile):
    lat, lng = house.get("latitude"), house.get("longitude")
    try:
        lat, lng = float(lat), float(lng)
    except (TypeError, ValueError):
        raise ValueError("この物件には位置情報(緯度・経度)がないため、ハザードの判定ができません。")

    fetch = _cached(fetch)
    # 判定と地図の取得をまとめて並列で行う(タイルを取りに行く待ち時間を短くするため)
    with ThreadPoolExecutor(max_workers=8) as pool:
        verdicts = list(pool.map(lambda h: judge_hazard(lat, lng, h, fetch), HAZARDS))
        base_cache: dict = {}
        # 下絵を先に1回取得してから、4種類の地図を並列に作る
        first = render_hazard_map(lat, lng, HAZARDS[0], fetch, base_cache)
        rest = list(pool.map(lambda h: render_hazard_map(lat, lng, h, fetch, base_cache), HAZARDS[1:]))
    maps = [first] + rest

    st = gp.build_styles()
    small = ParagraphStyle("small", parent=st["note"], fontSize=8, leading=11)
    cap = ParagraphStyle("cap", parent=st["body"], fontName="NotoSansJP-Bold", fontSize=10, leading=14)
    warn = ParagraphStyle("warn", parent=st["body"], fontSize=9, leading=14)

    buf = BytesIO() if output_path is None else output_path
    doc = SimpleDocTemplate(
        buf, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm, topMargin=14 * mm, bottomMargin=14 * mm,
        title="防災レポート(ハザード情報)", author="空き家AIプランナー",
    )
    story = []
    title = house.get("title") or f'{house.get("municipality", "")}の空き家'
    story += [_p("防災レポート(ハザード情報)", st["title"]), Spacer(1, 2 * mm),
              _p(f"{title}", st["body"]), _p(house.get("address") or "", st["address"]),
              HRFlowable(width="100%", thickness=0.6, color=gp.LINE, spaceBefore=4, spaceAfter=4)]

    hits = sum(1 for v in verdicts if v == "risk")
    if all(v == "error" for v in verdicts):
        summary = "ハザード情報を取得できませんでした。時間をおいて、もう一度お試しください。"
    elif hits:
        names = "・".join(h["label"] for h, v in zip(HAZARDS, verdicts) if v == "risk")
        summary = f"4種類のうち{hits}件({names})に、含まれる可能性があります。"
    else:
        summary = "4種類のハザードとも、指定区域外の可能性があります(安全を保証するものではありません)。"
    box = Table([[_p(f"<b>判定のまとめ</b>:{summary}", st["body"])]], colWidths=[178 * mm])
    box.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), gp.PANEL), ("BOX", (0, 0), (-1, -1), 0.5, gp.LINE),
                             ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                             ("LEFTPADDING", (0, 0), (-1, -1), 8)]))
    story += [box, Spacer(1, 4 * mm)]

    cells = []
    for h, v, m in zip(HAZARDS, verdicts, maps):
        color = VERDICT_COLOR[v].hexval()[2:]
        if m is not None:
            bio = BytesIO(); m.save(bio, "JPEG", quality=85); bio.seek(0)
            pic = RLImage(bio, width=84 * mm, height=84 * mm * MAP_SIZE[1] / MAP_SIZE[0])
        else:
            pic = _p("(地図を取得できませんでした)", small)
        cells.append([
            _p(h["label"], cap), pic,
            _p(f'<font color="#{color}"><b>{VERDICT_TEXT[v]}</b></font>', st["body"]),
            _p(h["note"], small),
        ])
    grid = Table([[cells[0], cells[1]], [cells[2], cells[3]]], colWidths=[89 * mm, 89 * mm])
    grid.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    story += [grid]
    story += [_p("地図の赤い丸が、この物件の位置です。色のついた範囲が、そのハザードの想定区域です(色の濃さは、浸水の深さなどのランクを表します)。"
                 "ランクごとの色は、ハザードマップポータルサイトの公式の凡例で確認してください。", small),
              Spacer(1, 3 * mm)]

    need = [h["key"] for h, v in zip(HAZARDS, verdicts) if v == "risk" and h["key"] in sh.FLAG_LABELS]
    story += _shelter_section(lat, lng, need, st, small, warn)

    # ---- 2ページ目: チェックリストと注意書き ----
    story += [CondPageBreak(110 * mm), _p("備えておきたいもの(チェックリスト)", st["section"]),
              _p("市区町村・内閣府などの防災の案内にある一般的な例をもとにした目安です。家族や、ペット・赤ちゃん・高齢の方がいる場合は、必要なものを足してください。", st["note"]),
              Spacer(1, 2 * mm)]
    for heading, items in CHECKLIST:
        story.append(_p(f"<b>{heading}</b>", st["body"]))
        rows = []
        for i in range(0, len(items), 2):
            pair = items[i:i + 2]
            rows.append([_p("□ " + t, warn) for t in pair] + [""] * (2 - len(pair)))
        t = Table(rows, colWidths=[89 * mm, 89 * mm])
        t.setStyle(TableStyle([("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                               ("VALIGN", (0, 0), (-1, -1), "TOP")]))
        story += [t, Spacer(1, 2 * mm)]

    story += [Spacer(1, 3 * mm), _p("ご利用にあたっての注意", st["section"])]
    notes = [
        "このレポートは、国のハザードマップのデータを使った簡易判定による参考情報です。安全を保証するものではありません。「指定区域外」でも、被害が出ることがあります。",
        "物件の位置は、住所から求めた代表地点(町や丁目の中心付近など)の場合があり、実際の敷地とずれていることがあります。購入や移住の前に、現地で必ずご確認ください。",
        "正式なハザードマップや、避難場所・避難所は、お住まい(予定)の市区町村の公式の発表をご確認ください。データは更新されるため、古い場合や、未掲載の場合があります。",
        "洪水・津波・高潮は「浸水の想定」、土砂災害は「警戒区域」です。想定を超える災害が起こることもあります。",
    ]
    for n in notes:
        story.append(_p("・" + n, st["note"]))
    story += [Spacer(1, 3 * mm),
              _p("出典: ハザードマップポータルサイト(国土交通省)「重ねるハザードマップ」/ 背景の地図: 地理院タイル(国土地理院)", small),
              _p(f"作成日: {datetime.now().strftime('%Y年%m月%d日')}", small)]

    doc.build(story)
    if output_path is None:
        buf.seek(0)
        return buf
    return output_path