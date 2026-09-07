import json
import glob
import os

# 1. テーブル定義（指定された最新スキーマ）
SCHEMA_HEADER = """-- ==========================================
-- テーブルの削除と再作成
-- ==========================================
DROP TABLE IF EXISTS houses;
DROP TABLE IF EXISTS subsidies;

CREATE TABLE subsidies (
    id INT PRIMARY KEY,
    prefecture VARCHAR(50) NOT NULL,
    municipality VARCHAR(50),
    subsidy_name VARCHAR(100) NOT NULL,
    target_type VARCHAR(50),
    max_amount INT,
    rate VARCHAR(50),
    conditions TEXT
);

CREATE TABLE houses (
    id INT PRIMARY KEY,
    transaction_type VARCHAR(20) NOT NULL, -- '売買' または '賃貸'
    title VARCHAR(100) NOT NULL,
    prefecture VARCHAR(50) NOT NULL,      -- 都道府県名
    municipality VARCHAR(50) NOT NULL,    -- 市区町村名
    address TEXT,
    price INT,                             -- 売買価格（円）
    rent_monthly INT,                      -- 月額家賃（円）
    layout VARCHAR(50),                    -- 間取り（3LDKなど）
    floors INT,
    land_area_m2 NUMERIC,
    building_area_m2 NUMERIC,
    tsubo NUMERIC,
    tsubo_unit_price_maruen NUMERIC,
    structure VARCHAR(50),
    built_year VARCHAR(50),
    renovation_cost_est INT,               -- 改修見積もり額（円）
    features TEXT,
    latitude NUMERIC,
    longitude NUMERIC
);

-- ==========================================
-- 全国データ投入
-- ==========================================
"""

def fmt_str(val):
    """文字列のエスケープ処理とSQL用整形"""
    if val is None:
        return "NULL"
    escaped = str(val).replace("'", "''")
    return f"'{escaped}'"

def fmt_num(val):
    """数値のSQL用整形"""
    if val is None or val == "":
        return "NULL"
        
    # カンマが入っている場合のクリーニング
    if isinstance(val, str):
        cleaned = val.replace(",", "").strip()
        if not cleaned:
            return "NULL"
        return cleaned
    return str(val)

def generate_combined_schema():
    json_files = glob.glob("data/*.json")
    
    if not json_files:
        print("data/ フォルダ内に JSON ファイルが見つかりません。")
        return

    total_count = 0
    
    with open("schema.sql", "w", encoding="utf-8") as out_f:
        out_f.write(SCHEMA_HEADER)
        
        for file_path in sorted(json_files):
            file_name = os.path.basename(file_path)
            print(f"読み込み中: {file_name}")
            
            with open(file_path, "r", encoding="utf-8") as in_f:
                houses = json.load(in_f)
                
            out_f.write(f"\n-- {file_name} 由来のデータ ({len(houses)}件)\n")
            
            for h in houses:
                sql = f"""INSERT INTO houses (
    id, transaction_type, title, prefecture, municipality, address,
    price, rent_monthly, layout, floors, land_area_m2, building_area_m2,
    tsubo, tsubo_unit_price_maruen, structure, built_year, renovation_cost_est,
    features, latitude, longitude
) VALUES (
    {h['id']},
    {fmt_str(h.get('transaction_type', '売買'))},
    {fmt_str(h.get('title', ''))},
    {fmt_str(h.get('prefecture', ''))},
    {fmt_str(h.get('municipality', ''))},
    {fmt_str(h.get('address'))},
    {fmt_num(h.get('price'))},
    {fmt_num(h.get('rent_monthly'))},
    {fmt_str(h.get('layout'))},
    {fmt_num(h.get('floors'))},
    {fmt_num(h.get('land_area_m2'))},
    {fmt_num(h.get('building_area_m2'))},
    {fmt_num(h.get('tsubo'))},
    {fmt_num(h.get('tsubo_unit_price_maruen'))},
    {fmt_str(h.get('structure'))},
    {fmt_str(h.get('built_year'))},
    {fmt_num(h.get('renovation_cost_est'))},
    {fmt_str(h.get('features'))},
    {fmt_num(h.get('latitude'))},
    {fmt_num(h.get('longitude'))}
);\n"""
                out_f.write(sql)
                
            total_count += len(houses)

    print(f"\n完了！合計 {len(json_files)} 個のファイル（計 {total_count} 件）から schema.sql を更新しました。")

if __name__ == "__main__":
    generate_combined_schema()