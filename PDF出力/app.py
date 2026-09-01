import os
from io import BytesIO
from flask import Flask, request, jsonify, send_file, send_from_directory
from flask_cors import CORS
import generate_proposal

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(CURRENT_DIR)
# 「フロント」フォルダのパスを正確に取得
FRONTEND_DIR = os.path.join(BASE_DIR, "フロント")

app = Flask(__name__, static_folder=FRONTEND_DIR, static_url_path="")
CORS(app)

# http://localhost:5000/ で index.html を配信
@app.route('/')
def index():
    return send_from_directory(FRONTEND_DIR, 'index.html')

# 物件リストAPI
@app.route('/api/properties', methods=['GET'])
def get_properties():
    try:
        input_json = os.path.join(BASE_DIR, "houses.json")
        properties = generate_proposal.load_properties(input_json)
        
        # グローバルに一意な ID を割り当て
        for idx, prop in enumerate(properties, start=1):
            prop['global_id'] = idx
        
        return jsonify(properties)
    except Exception as e:
        print(f"Error: {e}")
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

        input_json = os.path.join(BASE_DIR, "houses.json")
        properties = generate_proposal.load_properties(input_json)
        
        # グローバル ID でマッピング（1-indexed）
        if 1 <= house_id <= len(properties):
            target = properties[house_id - 1]
        else:
            target = properties[0]
        
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
    print(f"📁 フロントエンド参照元: {FRONTEND_DIR}")
    print("🚀 サーバー起動中: http://localhost:8080")
    app.run(debug=True, port=8080)