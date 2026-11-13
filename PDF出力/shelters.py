"""
避難所データ(国土地理院「指定緊急避難場所」「指定避難所」のCSV)を読み込み、
指定した地点から近い順に探す。

データの置き場所: <リポジトリ>/data/shelters/
  ・shelters_emergency*.csv   指定緊急避難場所(災害から「逃げる」場所)
  ・shelters_designated*.csv  指定避難所(避難生活を「送る」場所)
  環境変数 SHELTER_DATA_DIR で置き場所を変えられる。

距離は直線距離。実際の道のりはもっと長くなる。徒歩の目安は 80m/分。
データは更新されるため、古い場合や未掲載の場合がある(自治体の公式発表を確認すること)。
出典: 国土地理院「指定緊急避難場所データ」「指定避難所データ」
"""
from __future__ import annotations

import csv
import glob
import math
import os
from functools import lru_cache
from typing import Optional

WALK_M_PER_MIN = 80

# 指定緊急避難場所CSVの「対応する災害」の列 → 内部キー・表示名
EMERGENCY_FLAG_COLUMNS = {
    "洪水": ("flood", "洪水"),
    "崖崩れ、土石流及び地滑り": ("sediment", "土砂災害"),
    "高潮": ("hightide", "高潮"),
    "地震": ("quake", "地震"),
    "津波": ("tsunami", "津波"),
    "大規模な火事": ("fire", "大規模火事"),
    "内水氾濫": ("inland", "内水氾濫"),
    "火山現象": ("volcano", "火山"),
}
FLAG_LABELS = {k: v for k, v in EMERGENCY_FLAG_COLUMNS.values()}

ATTRIBUTION = "出典: 国土地理院「指定緊急避難場所データ」「指定避難所データ」(市区町村が公表した情報をもとにしています)"


def data_dir() -> str:
    env = os.environ.get("SHELTER_DATA_DIR")
    if env:
        return env
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(os.path.dirname(here), "data", "shelters")


def _read_rows(path: str):
    last_err: Optional[Exception] = None
    for enc in ("utf-8-sig", "cp932"):
        try:
            with open(path, encoding=enc, newline="") as f:
                return list(csv.DictReader(f))
        except UnicodeDecodeError as e:
            last_err = e
    raise last_err  # type: ignore[misc]


def _to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


@lru_cache(maxsize=1)
def load_shelters():
    """{'emergency': [...], 'designated': [...]} を返す。ファイルがなければ空のリスト。"""
    d = data_dir()
    out = {"emergency": [], "designated": []}
    for kind, pattern in (("emergency", "shelters_emergency*.csv"), ("designated", "shelters_designated*.csv")):
        for path in sorted(glob.glob(os.path.join(d, pattern))):
            for r in _read_rows(path):
                lat, lng = _to_float(r.get("緯度")), _to_float(r.get("経度"))
                if lat is None or lng is None:
                    continue
                item = {
                    "id": r.get("共通ID", ""),
                    "name": (r.get("施設・場所名") or "").strip(),
                    "address": (r.get("住所") or "").strip(),
                    "municipality": (r.get("都道府県名及び市町村名") or "").strip(),
                    "lat": lat, "lng": lng,
                    "note": (r.get("備考") or "").strip(),
                }
                if kind == "emergency":
                    item["flags"] = {key: (r.get(col) or "").strip() == "1"
                                     for col, (key, _l) in EMERGENCY_FLAG_COLUMNS.items()}
                else:
                    item["target"] = (r.get("受入対象者") or "").strip()
                    item["also_emergency"] = (r.get("指定緊急避難場所との住所同一") or "").strip() == "1"
                out[kind].append(item)
    return out


def has_data() -> bool:
    d = load_shelters()
    return bool(d["emergency"] or d["designated"])


def _distance_m(lat1, lng1, lat2, lng2) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def nearest(lat: float, lng: float, kind: str, limit: int = 5, need: Optional[list] = None,
            max_km: float = 10.0):
    """近い順に limit 件。need=['flood',...] を渡すと、そのすべてに対応する避難場所を先に並べる
    (対応していないものは後ろに回し、'matches_need': False を付ける)。kind='designated' では need は無視。"""
    need = [n for n in (need or []) if n in FLAG_LABELS] if kind == "emergency" else []
    # 緯度経度の箱で先に絞り込む(全件で距離計算しないため)
    dlat = max_km / 111.0
    dlng = max_km / (111.0 * max(math.cos(math.radians(lat)), 0.2))
    found = []
    for s in load_shelters()[kind]:
        if abs(s["lat"] - lat) > dlat or abs(s["lng"] - lng) > dlng:
            continue
        d = _distance_m(lat, lng, s["lat"], s["lng"])
        if d > max_km * 1000:
            continue
        item = dict(s)
        item["distance_m"] = int(round(d))
        item["walk_min"] = max(1, int(round(d / WALK_M_PER_MIN)))
        if kind == "emergency":
            item["supports"] = [FLAG_LABELS[k] for k, on in s["flags"].items() if on]
            item["matches_need"] = all(s["flags"].get(k) for k in need) if need else True
        found.append(item)
    found.sort(key=lambda x: (not x.get("matches_need", True), x["distance_m"]))
    return found[:limit]