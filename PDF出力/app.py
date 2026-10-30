import os
import sqlite3
from io import BytesIO
from urllib.parse import quote

import requests
from dotenv import load_dotenv
from flask import Flask, Response, request, jsonify, send_file, send_from_directory
from flask_cors import CORS
import generate_proposal

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(CURRENT_DIR)
load_dotenv(os.path.join(BASE_DIR, ".env"))

# 空き家周辺の観光・景観スポット検索(Google Places API (New))に使うキー。
# 未設定の場合は機能自体を無効化し、/api/nearby-spots は空リストを返す
# (このキーはここでしか使わず、ブラウザには一切送信しない)。
GOOGLE_PLACES_API_KEY = os.environ.get("GOOGLE_PLACES_API_KEY", "").strip()
# 観光・景観系として検索する対象タイプ。Google側の仕様変更でタイプ名が
# 変わった場合はここだけ直せばよい。
# 参考: https://developers.google.com/maps/documentation/places/web-service/place-types
PLACES_SIGHTSEEING_TYPES = [
    "tourist_attraction",
    "park",
    "museum",
    "art_gallery",
    "historical_landmark",
]
# 「フロント」フォルダのパスを正確に取得
FRONTEND_DIR = os.path.join(BASE_DIR, "フロント")
DATABASE_PATH = os.path.join(BASE_DIR, "empty_house.db")
SCHEMA_PATH = os.path.join(BASE_DIR, "schema.sql")
PREFECTURES = (
    "北海道", "青森県", "岩手県", "宮城県", "秋田県", "山形県", "福島県",
    "茨城県", "栃木県", "群馬県", "埼玉県", "千葉県", "東京都", "神奈川県",
    "新潟県", "富山県", "石川県", "福井県", "山梨県", "長野県", "岐阜県",
    "静岡県", "愛知県", "三重県", "滋賀県", "京都府", "大阪府", "兵庫県",
    "奈良県", "和歌山県", "鳥取県", "島根県", "岡山県", "広島県", "山口県",
    "徳島県", "香川県", "愛媛県", "高知県", "福岡県", "佐賀県", "長崎県",
    "熊本県", "大分県", "宮崎県", "鹿児島県", "沖縄県",
)

app = Flask(__name__, static_folder=FRONTEND_DIR, static_url_path="")
CORS(app)


@app.after_request
def no_cache(response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


def init_database():
    if os.path.exists(DATABASE_PATH) and os.path.getmtime(DATABASE_PATH) >= os.path.getmtime(SCHEMA_PATH):
        return

    # Gunicornなど複数ワーカー/同時リクエスト環境で、構築中のDBファイルを他のリクエストが
    # 読みに行ってしまう(または複数ワーカーが同じファイルに同時書き込みしてしまう)のを防ぐため、
    # 一時ファイルに構築してから完成後にアトミックにリネームする。
    tmp_path = f"{DATABASE_PATH}.tmp.{os.getpid()}"
    try:
        with sqlite3.connect(tmp_path) as connection:
            with open(SCHEMA_PATH, encoding="utf-8") as schema_file:
                connection.executescript(schema_file.read())
        os.replace(tmp_path, DATABASE_PATH)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def load_properties_from_database():
    init_database()
    with sqlite3.connect(DATABASE_PATH) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT * FROM houses ORDER BY id"
        ).fetchall()

    properties = []
    for row in rows:
        property_data = dict(row)
        property_data["uid"] = f'{property_data["municipality"]}-{property_data["id"]}'
        property_data["global_id"] = property_data["id"]
        properties.append(property_data)
    return properties


def load_prefectures_from_database():
    properties = load_properties_from_database()
    addresses = [property_data.get("address") or "" for property_data in properties]
    return [prefecture for prefecture in PREFECTURES if any(address.startswith(prefecture) for address in addresses)]


def load_subsidies_from_database(municipality):
    init_database()
    with sqlite3.connect(DATABASE_PATH) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT * FROM subsidies WHERE municipality = ? ORDER BY id",
            (municipality,)
        ).fetchall()
    return [dict(row) for row in rows]


def fetch_nearby_sightseeing_spots(lat, lng, radius_m=3000, max_results=8):
    """空き家の緯度経度から近い観光・景観スポットをGoogle Places API (New)で検索する。

    APIキー未設定、通信失敗、該当なしのいずれの場合もエラーにはせず、
    spots=[] と note(理由)を返す(フロント側は「該当する補助金情報はありません」と
    同じ考え方で、取得できない場合は静かに「見つかりませんでした」を表示する想定)。
    """
    if not GOOGLE_PLACES_API_KEY:
        return {"spots": [], "note": "GOOGLE_PLACES_API_KEYが設定されていません。"}

    try:
        response = requests.post(
            "https://places.googleapis.com/v1/places:searchNearby",
            headers={
                "Content-Type": "application/json",
                "X-Goog-Api-Key": GOOGLE_PLACES_API_KEY,
                "X-Goog-FieldMask": (
                    "places.displayName,places.rating,places.userRatingCount,"
                    "places.formattedAddress,places.location,places.types,places.photos,"
                    "places.googleMapsUri"
                ),
            },
            json={
                "includedTypes": PLACES_SIGHTSEEING_TYPES,
                "maxResultCount": max_results,
                "rankPreference": "POPULARITY",
                "locationRestriction": {
                    "circle": {
                        "center": {"latitude": lat, "longitude": lng},
                        "radius": radius_m,
                    }
                },
            },
            timeout=8,
        )
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as e:
        print(f"Places API error: {e}")
        return {"spots": [], "note": "Google Places APIへの通信に失敗しました。"}

    spots = []
    for place in data.get("places", []):
        photos = place.get("photos") or []
        photo_name = photos[0].get("name") if photos else None
        location = place.get("location") or {}
        name = (place.get("displayName") or {}).get("text", "")
        # Googleマップ上のその場所のページへのリンク。googleMapsUriが取れない場合に
        # 備えて、名称+緯度経度からの検索リンクをフォールバックとして組み立てる。
        maps_url = place.get("googleMapsUri")
        if not maps_url and location.get("latitude") is not None:
            maps_url = (
                "https://www.google.com/maps/search/?api=1&query="
                f"{quote(name)}+{location.get('latitude')},{location.get('longitude')}"
            )
        spots.append({
            "name": name,
            "rating": place.get("rating"),
            "user_rating_count": place.get("userRatingCount"),
            "address": place.get("formattedAddress"),
            "lat": location.get("latitude"),
            "lng": location.get("longitude"),
            "types": place.get("types", []),
            "photo_url": f"/api/place-photo?name={quote(photo_name, safe='')}" if photo_name else None,
            "maps_url": maps_url,
        })
    return {"spots": spots, "note": None}

# Gunicorn等での起動時に一度だけDBを構築しておく(初回リクエストが複数同時に来て
# 競合するのを防ぐ)。失敗してもアプリ自体は起動させ、後続のリクエスト側の
# init_database() 呼び出しでリトライされるようにする。
try:
    init_database()
except Exception as e:  # noqa: BLE001
    print(f"起動時のDB初期化に失敗しました(リクエスト時に再試行されます): {e}")


# http://localhost:5000/ で index.html を配信
@app.route('/')
def index():
    return send_from_directory(FRONTEND_DIR, 'index.html')

# 物件リストAPI
@app.route('/api/properties', methods=['GET'])
def get_properties():
    try:
        return jsonify(load_properties_from_database())
    except Exception as e:
        print(f"Error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/prefectures', methods=['GET'])
def get_prefectures():
    try:
        return jsonify(load_prefectures_from_database())
    except Exception as e:
        print(f"Error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/subsidies', methods=['GET'])
def get_subsidies():
    try:
        municipality = request.args.get('municipality', '').strip()
        return jsonify(load_subsidies_from_database(municipality))
    except Exception as e:
        print(f"Error: {e}")
        return jsonify({"error": str(e)}), 500

# 周辺の観光・景観スポットAPI(Google Places API (New) をサーバー側で呼び出す)
@app.route('/api/nearby-spots', methods=['GET'])
def get_nearby_spots():
    try:
        lat = request.args.get('lat', type=float)
        lng = request.args.get('lng', type=float)
        if lat is None or lng is None:
            return jsonify({"spots": [], "note": "lat/lngが指定されていません。"}), 400
        return jsonify(fetch_nearby_sightseeing_spots(lat, lng))
    except Exception as e:
        print(f"Error: {e}")
        return jsonify({"error": str(e)}), 500


# 観光・景観スポットの写真プロキシ(APIキーをブラウザに渡さないため、ここで中継する)
@app.route('/api/place-photo', methods=['GET'])
def get_place_photo():
    name = request.args.get('name', '').strip()
    if not name or not GOOGLE_PLACES_API_KEY:
        return '', 404
    try:
        response = requests.get(
            f"https://places.googleapis.com/v1/{name}/media",
            params={"maxWidthPx": 480, "key": GOOGLE_PLACES_API_KEY},
            timeout=8,
        )
        response.raise_for_status()
    except requests.RequestException as e:
        print(f"Places photo error: {e}")
        return '', 502
    return Response(response.content, mimetype=response.headers.get('Content-Type', 'image/jpeg'))


# PDF生成API
@app.route('/api/generate-pdf', methods=['GET', 'POST'])
def generate_pdf_api():
    try:
        if request.method == 'GET':
            house_id = request.args.get('id', default=1, type=int)
        else:
            # POST の場合は force=True で JSON パースエラーを回避
            data = request.get_json(force=True, silent=True) or {}
            house_id = data.get('id', 1)

        properties = load_properties_from_database()
        
        # グローバル ID でマッピング（1-indexed）
        if 1 <= house_id <= len(properties):
            target = properties[house_id - 1]
        else:
            target = properties[0]

        target["subsidies"] = load_subsidies_from_database(target["municipality"])
        
        # メモリ上で PDF を生成
        pdf_buffer = generate_proposal.build_proposal(target, output_path=None)
        
        # ダウンロード用のファイル名を生成
        filename = f'proposal_{target["uid"]}.pdf'
        
        return send_file(
            pdf_buffer,
            mimetype='application/pdf',
            as_attachment=True,
            download_name=filename
        )
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    init_database()
    print(f"📁 フロントエンド参照元: {FRONTEND_DIR}")
    print("🚀 サーバー起動中: http://localhost:8080")
    app.run(debug=True, host='0.0.0.0', port=8080)