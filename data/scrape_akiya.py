import re
import time
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.akiya-athome.jp"
START_URL = "https://www.akiya-athome.jp/buy/03/?br_kbn=buy&pref_cd=03&page=2&search_sort=kokai_date&item_count=100"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}


def clean_text(text):
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def extract_number(text):
    if not text:
        return "NULL"
    num_str = re.sub(r"\D", "", text)
    return int(num_str) if num_str else "NULL"


def extract_float(text):
    if not text:
        return "NULL"
    match = re.search(r"\d+(\.\d+)?", text)
    return float(match.group()) if match else "NULL"


def parse_price(price_str):
    if not price_str or "相談" in price_str or "未定" in price_str:
        return "NULL"
    match = re.search(r"(\d+(?:\.\d+)?)\s*万", price_str)
    if match:
        val = float(match.group(1))
        return int(val * 10000)
    num = extract_number(price_str)
    return num if num != "NULL" else "NULL"


def get_detail_urls(start_url, max_pages=1):
    detail_urls = []
    current_url = start_url
    page_count = 0

    while current_url and page_count < max_pages:
        page_count += 1
        print(f"--- 一覧ページ {page_count} ページ目を処理中 ---")
        time.sleep(2)

        res = requests.get(current_url, headers=HEADERS)
        if res.status_code != 200:
            break

        res.encoding = res.apparent_encoding
        soup = BeautifulSoup(res.text, "html.parser")

        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "/bukken/detail/" in href or "/detail/" in href:
                full_url = urljoin(BASE_URL, href)
                if full_url not in detail_urls:
                    detail_urls.append(full_url)

        # 次へボタン
        next_btn = soup.find("a", string=re.compile(r"次へ"))
        if next_btn and next_btn.get("href"):
            current_url = urljoin(BASE_URL, next_btn.get("href"))
        else:
            current_url = None

    return detail_urls


def scrape_detail_page(url, house_id):
    time.sleep(2)  # 2秒待機
    res = requests.get(url, headers=HEADERS)
    if res.status_code != 200:
        return None

    res.encoding = res.apparent_encoding
    soup = BeautifulSoup(res.text, "html.parser")

    # --- 1. タイトル取得 ---
    title_el = (
        soup.select_one("h1")
        or soup.select_one(".p-detail-title")
        or soup.select_one("title")
    )
    title = clean_text(title_el.get_text()) if title_el else "無題"
    title = title.replace("'", "''")

    # --- 2. テーブル（th と td のキー＆バリュー）を精密にペアリング ---
    info = {}
    for table in soup.find_all("table"):
        for tr in table.find_all("tr"):
            ths = tr.find_all("th")
            tds = tr.find_all("td")
            # th と td が同数の場合（1行に複数項目入っているケースに対応）
            if len(ths) == len(tds) and len(ths) > 0:
                for th, td in zip(ths, tds):
                    k = clean_text(th.get_text())
                    v = clean_text(td.get_text())
                    if k:
                        info[k] = v
            # th1つに対してtd1つの基本ケース
            elif len(ths) == 1 and len(tds) >= 1:
                k = clean_text(ths[0].get_text())
                v = clean_text(tds[-1].get_text())
                if k:
                    info[k] = v

    # --- 3. テーブル外からのバックアップ取得（住所・価格など） ---
    # 住所を取得できるキーワードを網羅検索
    raw_address = ""
    for key in [
        "所在地",
        "住所",
        "交通/所在地",
        "交通・所在地",
        "物件所在地",
    ]:
        if key in info and info[key]:
            raw_address = info[key]
            break

    # テーブルから取れなかった場合、ページ内の特定クラス・テキストからフォールバック
    if not raw_address:
        addr_el = soup.select_one(".address, .p-detail-address, [class*='address']")
        if addr_el:
            raw_address = clean_text(addr_el.get_text())

    address = raw_address.replace("'", "''")

    # 都道府県・市区町村の判定
    pref_match = re.match(
        r"(東京都|北海道|(?:京都|大阪)府|.{2,3}県)(.*?[市区町村群])", address
    )
    if pref_match:
        prefecture = pref_match.group(1)
        municipality = pref_match.group(2)
    else:
        # パンくずリスト等から都道府県を補完
        prefecture = "不明"
        municipality = "不明"
        breadcrumbs = [
            clean_text(a.get_text()) for a in soup.select("ol li, ul.breadcrumb li")
        ]
        for bc in breadcrumbs:
            m = re.search(
                r"(東京都|北海道|(?:京都|大阪)府|.{2,3}県)", bc
            )
            if m:
                prefecture = m.group(1)
                break

    # 価格
    raw_price = ""
    for key in ["価格", "物件価格", "売買価格", "希望価格"]:
        if key in info and info[key]:
            raw_price = info[key]
            break
    price = parse_price(raw_price)

    # 間取り
    layout = info.get("間取り", info.get("間取り内訳", "")).replace(
        "'", "''"
    )

    # 土地面積・建物面積
    land_area = extract_float(
        info.get("土地面積", info.get("敷地面積", ""))
    )
    building_area = extract_float(
        info.get("建物面積", info.get("延床面積", ""))
    )

    # 構造・築年
    structure = info.get("構造", info.get("建物構造", "")).replace(
        "'", "''"
    )
    built_year = info.get(
        "築年月", info.get("建築年月", info.get("完成時期", ""))
    ).replace("'", "''")

    # SQLの書き出し（文字列項目のクォート囲みとNULLハンドリング）
    fmt_str = lambda val: f"'{val}'" if val else "NULL"

    sql = f"""INSERT INTO houses (
    id, transaction_type, title, prefecture, municipality, address,
    price, rent_monthly, layout, floors, land_area_m2, building_area_m2,
    structure, built_year
) VALUES (
    {house_id},
    '売買',
    {fmt_str(title)},
    {fmt_str(prefecture)},
    {fmt_str(municipality)},
    {fmt_str(address)},
    {price},
    NULL,
    {fmt_str(layout)},
    NULL,
    {land_area},
    {building_area},
    {fmt_str(structure)},
    {fmt_str(built_year)}
);"""
    return sql


if __name__ == "__main__":
    # まずは動作確認用に 1 ページ分取得
    urls = get_detail_urls(START_URL, max_pages=1)
    print(f"\n取得対象: {len(urls)} 件の詳細URL")

    sql_list = []
    for idx, d_url in enumerate(urls, start=1):
        print(f"[{idx}/{len(urls)}] 処理中: {d_url}")
        sql = scrape_detail_page(d_url, house_id=idx)
        if sql:
            sql_list.append(sql)

    with open("output_houses.sql", "a", encoding="utf-8") as f:
        f.write("\n\n".join(sql_list))

    print("\n完了！ output_houses.sql を確認してください。")