-- 既存のテーブルを削除して初期化
DROP TABLE IF EXISTS houses;
DROP TABLE IF EXISTS subsidies;

-- ==========================================
-- 1. 補助金テーブル (subsidies)
-- ==========================================
CREATE TABLE subsidies (
    id INT PRIMARY KEY,
    prefecture VARCHAR(50) NOT NULL,   -- 都道府県名（例: 千葉県, 東京都）
    municipality VARCHAR(50),           -- 市区町村名
    subsidy_name VARCHAR(100) NOT NULL,
    target_type VARCHAR(50),            -- 改修, 取得, 家賃補助, 引っ越し 等
    max_amount INT,                     -- 上限額（円）
    rate VARCHAR(50),                   -- 補助率（例: 1/2以内）
    conditions TEXT                     -- 対象条件
);

-- ==========================================
-- 2. 空き家テーブル (houses)
-- ==========================================
CREATE TABLE houses (
    id INT PRIMARY KEY,
    transaction_type VARCHAR(20) NOT NULL, -- '売買' または '賃貸'
    title VARCHAR(100) NOT NULL,
    prefecture VARCHAR(50) NOT NULL,      -- 都道府県名
    municipality VARCHAR(50) NOT NULL,    -- 市区町村名
    address TEXT,
    price INT,                             -- 売買価格（円）※賃貸の場合はNULL可
    rent_monthly INT,                      -- 月額家賃（円）※売買の場合はNULL可
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