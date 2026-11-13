import math
import os
import sqlite3
from io import BytesIO
from urllib.parse import quote

import requests
from dotenv import load_dotenv
from flask import Flask, Response, request, jsonify, send_file, send_from_directory
from flask_cors import CORS
import generate_proposal
import hazard_report
import shelters
import image_gen

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


def _haversine_distance_m(lat1, lng1, lat2, lng2):
    """2点間の距離をメートルで返す(球面近似、簡易版)。空き家からスポットまでの距離表示に使う。"""
    r = 6371000.0  # 地球の半径(m)
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


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
        spot_lat = location.get("latitude")
        spot_lng = location.get("longitude")
        distance_m = (
            round(_haversine_distance_m(lat, lng, spot_lat, spot_lng))
            if spot_lat is not None and spot_lng is not None
            else None
        )
        spots.append({
            "name": name,
            "rating": place.get("rating"),
            "user_rating_count": place.get("userRatingCount"),
            "address": place.get("formattedAddress"),
            "lat": spot_lat,
            "lng": spot_lng,
            "distance_m": distance_m,
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


# 空き家の場所のストリートビュー写真(メタデータ確認)API。
# 実物件の写真データは収集していないため、代わりにその場所のGoogleストリートビューを使う。
# 撮影範囲外の場所も多いため、まずmetadata APIで「その地点にストリートビュー画像があるか」
# だけを確認する(metadata APIは画像取得と違って無料・クオータを消費しない)。
# 無ければフロント側は「ストリートビューが見つかりませんでした」の表示に切り替える想定。
@app.route('/api/street-view', methods=['GET'])
def get_street_view():
    lat = request.args.get('lat', type=float)
    lng = request.args.get('lng', type=float)
    if lat is None or lng is None:
        return jsonify({"available": False, "note": "lat/lngが指定されていません。"}), 400
    if not GOOGLE_PLACES_API_KEY:
        return jsonify({"available": False, "note": "GOOGLE_PLACES_API_KEYが設定されていません。"})
    try:
        meta_res = requests.get(
            "https://maps.googleapis.com/maps/api/streetview/metadata",
            params={"location": f"{lat},{lng}", "key": GOOGLE_PLACES_API_KEY},
            timeout=6,
        )
        meta = meta_res.json()
    except (requests.RequestException, ValueError) as e:
        print(f"Street View metadata error: {e}")
        return jsonify({"available": False, "note": "Street View APIへの通信に失敗しました。"})

    if meta.get("status") != "OK":
        # "REQUEST_DENIED"(APIキーでStreet View Static APIが有効化されていない等)も
        # ここに含まれる。エラーにはせず、フロント側は静かに「見つかりませんでした」を表示する。
        return jsonify({"available": False, "note": meta.get("status")})

    return jsonify({
        "available": True,
        "image_url": f"/api/street-view-image?lat={lat}&lng={lng}",
    })


# 上記で存在確認が取れた場合に実際の画像を取得するAPI(APIキーをブラウザに渡さないため中継する)。
@app.route('/api/street-view-image', methods=['GET'])
def get_street_view_image():
    lat = request.args.get('lat', type=float)
    lng = request.args.get('lng', type=float)
    if lat is None or lng is None or not GOOGLE_PLACES_API_KEY:
        return '', 404
    try:
        response = requests.get(
            "https://maps.googleapis.com/maps/api/streetview",
            params={
                "size": "640x400",
                "location": f"{lat},{lng}",
                "fov": 80,
                "key": GOOGLE_PLACES_API_KEY,
            },
            timeout=8,
        )
        response.raise_for_status()
    except requests.RequestException as e:
        print(f"Street View image error: {e}")
        return '', 502
    return Response(response.content, mimetype=response.headers.get('Content-Type', 'image/jpeg'))


# 空き家の「活用イメージ」画像生成API(Geminiの画像生成モデルをサーバー側で呼び出す)。
# 選んだ物件の実データ(構造・築年・間取り等)と、ユーザーが指定した用途・雰囲気を
# プロンプトに組み込むことで、物件ごと・用途ごとに異なる画像を生成する。
@app.route('/api/generate-house-image', methods=['POST'])
def generate_house_image_api():
    try:
        data = request.get_json(force=True, silent=True) or {}
        house_id = data.get('id')
        use_type = (data.get('use_type') or '').strip()
        mood = (data.get('mood') or '').strip() or None

        if not house_id or not use_type:
            return jsonify({"error": "id と use_type は必須です。"}), 400

        properties = load_properties_from_database()
        target = next((p for p in properties if str(p.get('global_id')) == str(house_id)), None)
        if target is None:
            return jsonify({"error": "指定された物件が見つかりませんでした。"}), 404

        result = image_gen.generate_house_image(target, use_type, mood)
        if "error" in result:
            return jsonify(result), 502
        return jsonify(result)
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


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

# 近くの避難場所・避難所API(data/shelters/ のCSVを使う)
@app.route('/api/shelters', methods=['GET'])
def get_shelters():
    try:
        lat = request.args.get('lat', type=float)
        lng = request.args.get('lng', type=float)
        if lat is None or lng is None:
            return jsonify({"error": "lat と lng を指定してください"}), 400
        limit = min(request.args.get('limit', default=5, type=int), 20)
        need = [k for k in request.args.get('need', '').split(',') if k]
        return jsonify({
            "available": shelters.has_data(),
            "emergency": shelters.nearest(lat, lng, "emergency", limit=limit, need=need),
            "designated": shelters.nearest(lat, lng, "designated", limit=limit),
            "attribution": shelters.ATTRIBUTION,
        })
    except Exception as e:
        print(f"Error: {e}")
        return jsonify({"error": "避難所の情報を取得できませんでした"}), 500


# 防災レポート(ハザード情報)PDF生成API
@app.route('/api/generate-hazard-pdf', methods=['GET'])
def generate_hazard_pdf_api():
    try:
        house_id = request.args.get('id', default=1, type=int)
        properties = load_properties_from_database()
        if 1 <= house_id <= len(properties):
            target = properties[house_id - 1]
        else:
            target = properties[0]

        pdf_buffer = hazard_report.build_hazard_report(target)
        return send_file(
            pdf_buffer,
            mimetype='application/pdf',
            as_attachment=True,
            download_name=f'hazard_report_{target["uid"]}.pdf'
        )
    except ValueError as e:
        # 位置情報がない物件など、利用者に伝えられる理由
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": "防災レポートを作成できませんでした。時間をおいて、もう一度お試しください。"}), 500

if __name__ == '__main__':
    init_database()
    print(f"📁 フロントエンド参照元: {FRONTEND_DIR}")
    print("🚀 サーバー起動中: http://localhost:8080")
    app.run(debug=True, host='0.0.0.0', port=8080)