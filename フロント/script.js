let map, chartInstance;
let propertyData = [];
let selectedMarker = null;
let defaultMarkersGroup = null;

const API_BASE_URL = window.location.protocol === 'file:' ? 'http://127.0.0.1:8080' : '';
const CHAT_URL = "https://empty-house2026.onrender.com/diagnosis";
const chatLink = document.getElementById("chat-link");
if (chatLink) chatLink.href = CHAT_URL;

const AMENITY_RADIUS_M = 1000;
const amenityLayers = {};
const amenityVisible = {};
let amenityRequestId = 0;

// 選択中の物件ピン（青）
const SELECTED_HOUSE_ICON = L.divIcon({
    html: `<div style="background:linear-gradient(135deg,#22344B,#5C7A99);width:44px;height:44px;border-radius:50% 50% 50% 0;
                transform:rotate(-45deg);display:flex;align-items:center;justify-content:center;
                box-shadow:0 0 0 3px rgba(251,249,244,.95),0 8px 16px rgba(34,52,75,.35);
                border:2px solid #FBF9F4;">
                <span style="transform:rotate(45deg);font-size:18px;">🏠</span>
           </div>`,
    className: '',
    iconSize: [44, 44],
    iconAnchor: [22, 44],
    popupAnchor: [0, -36],
});

// 通常の物件ピン（金）
const UNSELECTED_HOUSE_ICON = L.divIcon({
    html: `<div style="background:#B0862A;width:30px;height:30px;border-radius:50% 50% 50% 0;
                transform:rotate(-45deg);display:flex;align-items:center;justify-content:center;
                box-shadow:0 2px 4px rgba(0,0,0,0.2);border:2px solid white;">
                <span style="transform:rotate(45deg);font-size:12px;color:white;">🏡</span>
           </div>`,
    className: '',
    iconSize: [30, 30],
    iconAnchor: [15, 30],
    popupAnchor: [0, -26],
});

function createAmenityIcon(iconHtml, color) {
    return L.divIcon({
        html: `<div style="background-color: ${color}; width: 26px; height: 26px; border-radius: 50% 50% 50% 0;
                transform: rotate(-45deg); display: flex; align-items: center; justify-content: center;
                border: 2px solid white; box-shadow: 0 2px 4px rgba(0,0,0,0.25);">
                <span style="transform: rotate(45deg); font-size: 12px; color: white;">${iconHtml}</span>
            </div>`,
        className: '',
        iconSize: [26, 26],
        iconAnchor: [13, 26],
        popupAnchor: [0, -22]
    });
}

const AMENITY_CONFIG = {
    school: { query: 'nwr["amenity"="school"]', icon: createAmenityIcon('🎓', '#B0862A'), symbol: '🎓', label: '学校' },
    supermarket: { query: 'nwr["shop"="supermarket"]', icon: createAmenityIcon('🛒', '#5F7350'), symbol: '🛒', label: 'スーパー' },
    station: { query: 'nwr["railway"="station"]', icon: createAmenityIcon('🚃', '#5C7A99'), symbol: '🚃', label: '駅' },
    hospital: { query: 'nwr["amenity"="hospital"]', icon: createAmenityIcon('🏥', '#954C36'), symbol: '🏥', label: '病院' },
};

document.addEventListener("DOMContentLoaded", () => {
    initMap();
    fetchProperties();

    const prefSelect = document.getElementById('prefSelect');
    if (prefSelect) {
        prefSelect.addEventListener('change', (e) => filterProperties(e.target.value));
    }
});

function initMap() {
    map = L.map('map', {
        zoomControl: false,
        attributionControl: true,
        scrollWheelZoom: true,
    }).setView([35.689, 139.691], 10);

    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
        attribution: '&copy; OpenStreetMap contributors',
        maxZoom: 19,
    }).addTo(map);

    defaultMarkersGroup = L.markerClusterGroup({
        showCoverageOnHover: false,
        maxClusterRadius: 45,
        disableClusteringAtZoom: 15  // 拡大して1件を見ている時は、絶対に数字付きクラスターにしない
    });

    L.control.zoom({ position: 'bottomright' }).addTo(map);

    const ResetControl = L.Control.extend({
        options: { position: 'bottomright' },
        onAdd: function() {
            const btn = L.DomUtil.create('button', 'leaflet-bar leaflet-control');
            btn.innerHTML = '⟲';
            btn.title = '全体表示に戻す';
            btn.style.cssText = 'background:#fff; width:30px; height:30px; font-size:16px; cursor:pointer; line-height:30px; text-align:center; font-weight:bold; color:#22344B;';
            btn.onclick = function() {
                fitMapToVisibleProperties(propertyData);
            };
            return btn;
        }
    });
    map.addControl(new ResetControl());

    initAmenityLayers();
    addAmenityLegendControl();
}

async function fetchProperties() {
    try {
        const res = await fetch(`${API_BASE_URL}/api/properties`);
        propertyData = await res.json();

        const prefectureRes = await fetch(`${API_BASE_URL}/api/prefectures`);
        const prefectures = await prefectureRes.json();
        const prefSelect = document.getElementById('prefSelect');
        
        if (prefSelect) {
            prefSelect.innerHTML = '<option value="none">エリアを選択してください</option>';
            prefectures.forEach(prefecture => {
                const option = document.createElement('option');
                option.value = prefecture;
                option.textContent = prefecture;
                prefSelect.appendChild(option);
            });
        }

        const params = new URLSearchParams(window.location.search);

        // 診断フォーム(/diagnosis)がマッチした候補(?property_ids=1,2,3)の場合は、
        // 1件に絞らず、候補を一覧(カード+ピン)で表示する。
        const idsParam = params.get('property_ids');
        const matchedIds = idsParam ? idsParam.split(',').map((s) => s.trim()).filter(Boolean) : [];
        const matchedHouses = matchedIds.length
            ? propertyData.filter((h) => matchedIds.includes(String(h.global_id)))
            : [];

        // チャット画面の物件カードからの遷移(?property_id=123、1件のみ)の場合は、
        // その物件のエリアを自動選択したうえで、詳細パネルを開いた状態で表示する。
        const targetId = params.get('property_id');
        const targetHouse = targetId ? propertyData.find(h => String(h.global_id) === String(targetId)) : null;

        if (matchedHouses.length > 0) {
            renderPropertyCards(matchedHouses, true);
            plotMapMarkers(matchedHouses);
            fitMapToVisibleProperties(matchedHouses);
            updateSelection(matchedHouses[0].global_id);
        } else if (targetHouse) {
            const pref = guessPrefecture(targetHouse);
            if (pref && prefSelect) prefSelect.value = pref;
            filterProperties(pref || 'none');
            updateSelection(targetHouse.global_id);
        } else {
            filterProperties('none');
        }

    } catch (err) {
        console.error("物件データの読み込み失敗:", err);
        const container = document.getElementById('propertyCardList');
        if (container) container.innerHTML = '<p class="text-xs text-rose-600 p-2">データの取得に失敗しました</p>';
    }
}

function filterProperties(pref) {
    if (pref === 'none') {
        renderPropertyCards([]);
        plotMapMarkers(propertyData);
        fitMapToVisibleProperties(propertyData);
        clearDetail();
        return;
    }

    const filtered = propertyData.filter(house => {
        const addr = house.address || house.location || '';
        return addr.includes(pref);
    });

    renderPropertyCards(filtered);
    plotMapMarkers(filtered);
    fitMapToVisibleProperties(filtered);
    clearDetail();
}

function renderPropertyCards(properties, forceShow) {
    const container = document.getElementById('propertyCardList');
    if (!container) return;
    container.innerHTML = '';

    const prefSelect = document.getElementById('prefSelect');
    const selectedPref = prefSelect ? prefSelect.value : 'none';

    // エリア未選択（'none'）の時はメッセージのみ表示してカードを出さない
    // (診断フォームからの検索結果表示時は forceShow=true でこのガードを飛ばす)
    if (!forceShow && selectedPref === 'none') {
        container.innerHTML = '<p class="text-xs text-slate-500 py-3 font-medium">エリアを選択すると物件一覧が表示されます</p>';
        return;
    }

    if (properties.length === 0) {
        container.innerHTML = '<p class="text-xs text-slate-500 py-3">該当する物件がありません</p>';
        return;
    }

    properties.forEach((house, index) => {
        const id = house.global_id;
        const name = house.name || house.title || '物件';

        const tags = house.tags || generateDefaultTags(index);

        const card = document.createElement('div');
        card.className = 'prop-card';
        card.dataset.id = id;
        
        card.innerHTML = `
            <p class="font-bold text-xs text-[#22344B] truncate mb-2">${name}</p>
            <div class="flex flex-wrap gap-1">
                ${tags.map(t => `<span class="text-[9px] px-1.5 py-0.5 rounded font-medium ${t.bg}">${t.label}</span>`).join('')}
            </div>
        `;

        card.addEventListener('click', () => {
            updateSelection(id);
        });

        container.appendChild(card);
    });
}

function generateDefaultTags(index) {
    const tagSets = [
        [
            { label: '🎓 学校徒歩圏', bg: 'bg-amber-100 text-amber-800 border border-amber-200' },
            { label: '🛒 スーパー近接', bg: 'bg-emerald-100 text-emerald-800 border border-emerald-200' },
            { label: '🚃 駅 10分', bg: 'bg-blue-100 text-blue-800 border border-blue-200' }
        ],
        [
            { label: '🛒 スーパー徒歩5分', bg: 'bg-emerald-100 text-emerald-800 border border-emerald-200' },
            { label: '🏥 総合病院近く', bg: 'bg-rose-100 text-rose-800 border border-rose-200' }
        ],
        [
            { label: '🚃 駅直結エリア', bg: 'bg-blue-100 text-blue-800 border border-blue-200' },
            { label: '🎓 学校近く', bg: 'bg-amber-100 text-amber-800 border border-amber-200' }
        ]
    ];
    return tagSets[index % tagSets.length];
}

function plotMapMarkers(properties) {
    defaultMarkersGroup.clearLayers();
    map.removeLayer(defaultMarkersGroup);
    
    properties.forEach((house) => {
        const coords = getCoordinates(house);
        const marker = L.marker(coords, { icon: UNSELECTED_HOUSE_ICON });
        
        marker.on('click', () => {
            updateSelection(house.global_id);
        });
        
        defaultMarkersGroup.addLayer(marker);
    });
    
    map.addLayer(defaultMarkersGroup);
}

function updateSelection(selectedId) {
    if (!selectedId) {
        clearDetail();
        return;
    }

    const house = propertyData.find(h => String(h.global_id) === String(selectedId));
    if (!house) return;

    document.querySelectorAll('.prop-card').forEach(card => {
        if (String(card.dataset.id) === String(selectedId)) {
            card.classList.add('active');
            card.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'center' });
        } else {
            card.classList.remove('active');
        }
    });

    document.getElementById('detailName').textContent = house.name || house.title || '-';
    const cost = house.renovation_cost_est || house.renovation_cost || house.cost || 0;
    document.getElementById('detailCost').textContent = cost > 0 ? `¥${cost.toLocaleString()}` : '¥3,000,000';
    document.getElementById('detailUsage').textContent = house.usage || house.purpose || '地域交流拠点';
    
    document.getElementById('detailAddress').textContent = `所在地: ${house.address || house.location || '-'}`;
    fetchSubsidies(house.municipality);

    const coords = getCoordinates(house);
    
    if (selectedMarker) map.removeLayer(selectedMarker);

    selectedMarker = L.marker(coords, { icon: SELECTED_HOUSE_ICON })
        .addTo(map)
        .bindPopup(`<b>📍 ${house.name || '選択中の物件'}</b>`)
        .openPopup();

    map.flyTo(coords, 14, { animate: true, duration: 0.9 });
    updateNearbyAmenities(coords[0], coords[1]);
    updateChart(house);
}

function clearDetail() {
    document.getElementById('detailName').textContent = '物件を選択してください';
    document.getElementById('detailCost').textContent = '-';
    document.getElementById('detailUsage').textContent = '-';
    document.getElementById('detailAddress').textContent = '所在地: -';
    
    const subsidyList = document.getElementById('subsidyList');
    if (subsidyList) subsidyList.innerHTML = '<p class="text-slate-500 py-4 text-center text-xs">物件を選択してください</p>';
    
    const statusEl = document.getElementById('amenityStatus');
    if (statusEl) statusEl.textContent = '物件を選択してください';
    
    document.querySelectorAll('.prop-card').forEach(card => card.classList.remove('active'));

    if (selectedMarker) {
        map.removeLayer(selectedMarker);
        selectedMarker = null;
    }
    Object.values(amenityLayers).forEach(layer => layer.clearLayers());
    updateChart(null);
}

function fitMapToVisibleProperties(filteredProperties) {
    if (!filteredProperties || filteredProperties.length === 0) return;
    const coords = filteredProperties.map(house => getCoordinates(house)).filter(Boolean);
    if (coords.length === 0) return;
    const bounds = L.latLngBounds(coords);
    if (bounds.isValid()) {
        map.fitBounds(bounds.pad(0.2), { animate: true, maxZoom: 14 });
    }
}

// 都道府県ごとの代表座標(県庁所在地付近)。実際の緯度経度データが無い物件のフォールバック用。
const PREF_COORDS = {
    '北海道': [43.0642, 141.3469], '青森県': [40.8244, 140.7400], '岩手県': [39.7036, 141.1527],
    '宮城県': [38.2688, 140.8721], '秋田県': [39.7186, 140.1024], '山形県': [38.2404, 140.3633],
    '福島県': [37.7503, 140.4676], '茨城県': [36.3418, 140.4468], '栃木県': [36.5658, 139.8836],
    '群馬県': [36.3911, 139.0608], '埼玉県': [35.8569, 139.6489], '千葉県': [35.6047, 140.1233],
    '東京都': [35.6895, 139.6917], '神奈川県': [35.4478, 139.6425], '新潟県': [37.9026, 139.0232],
    '富山県': [36.6953, 137.2113], '石川県': [36.5947, 136.6256], '福井県': [36.0652, 136.2216],
    '山梨県': [35.6642, 138.5685], '長野県': [36.6513, 138.1810], '岐阜県': [35.3912, 136.7223],
    '静岡県': [34.9769, 138.3831], '愛知県': [35.1802, 136.9066], '三重県': [34.7303, 136.5086],
    '滋賀県': [35.0045, 135.8686], '京都府': [35.0212, 135.7556], '大阪府': [34.6863, 135.5200],
    '兵庫県': [34.6913, 135.1830], '奈良県': [34.6851, 135.8049], '和歌山県': [34.2260, 135.1675],
    '鳥取県': [35.5036, 134.2381], '島根県': [35.4723, 133.0505], '岡山県': [34.6617, 133.9350],
    '広島県': [34.3966, 132.4596], '山口県': [34.1859, 131.4714], '徳島県': [34.0658, 134.5593],
    '香川県': [34.3401, 134.0434], '愛媛県': [33.8416, 132.7657], '高知県': [33.5597, 133.5311],
    '福岡県': [33.6064, 130.4181], '佐賀県': [33.2494, 130.2988], '長崎県': [32.7448, 129.8737],
    '熊本県': [32.7898, 130.7417], '大分県': [33.2382, 131.6126], '宮崎県': [31.9111, 131.4239],
    '鹿児島県': [31.5602, 130.5581], '沖縄県': [26.2124, 127.6809],
};
const PREF_NAMES = Object.keys(PREF_COORDS);

function guessPrefecture(house) {
    if (house.prefecture && PREF_COORDS[house.prefecture]) return house.prefecture;
    const address = house.address || house.location || '';
    return PREF_NAMES.find(name => address.startsWith(name)) || null;
}

function getCoordinates(house) {
    let lat = parseFloat(house.lat || house.latitude);
    let lng = parseFloat(house.lng || house.longitude);
    if (!isNaN(lat) && !isNaN(lng)) return [lat, lng];

    // 同じ都道府県内の物件が重ならないよう、IDから疑似乱数的に位置をずらす。
    // (以前は id % 10 / id % 7 で計算しており、組み合わせが70通りしかなく、
    // 件数の多い県では複数物件が完全に同じ座標に重なってしまっていた。
    // ハッシュ関数で連続的な疑似乱数を作ることで、重複がほぼ起きないようにする。)
    const id = house.global_id || 1;
    const hash1 = Math.abs(Math.sin(id * 12.9898) * 43758.5453) % 1;
    const hash2 = Math.abs(Math.sin(id * 78.233) * 12543.163) % 1;
    const angle = hash1 * Math.PI * 2;
    const radius = 0.05 + hash2 * 0.3;
    const latOffset = Math.cos(angle) * radius;
    const lngOffset = Math.sin(angle) * radius;

    const address = house.address || house.location || '';
    if (address.includes('奥多摩')) return [35.809 + latOffset, 139.096 + lngOffset];
    if (address.includes('羽生')) return [36.174 + latOffset, 139.551 + lngOffset];

    const prefecture = guessPrefecture(house);
    const base = (prefecture && PREF_COORDS[prefecture]) || PREF_COORDS['東京都'];
    return [base[0] + latOffset, base[1] + lngOffset];
}

async function fetchSubsidies(municipality) {
    const subsidyList = document.getElementById('subsidyList');
    if (!subsidyList) return;
    subsidyList.innerHTML = '<p class="text-slate-500 py-4 text-center text-xs">補助金情報を読み込み中...</p>';

    try {
        const res = await fetch(`${API_BASE_URL}/api/subsidies?municipality=${encodeURIComponent(municipality)}`);
        if (!res.ok) throw new Error();
        const subsidies = await res.json();

        if (subsidies.length === 0) {
            subsidyList.innerHTML = '<p class="text-slate-500 py-4 text-center text-xs">該当する補助金情報はありません</p>';
            return;
        }

        subsidyList.innerHTML = subsidies.map(subsidy => `
            <div class="subsidy-card p-2.5 text-xs">
                <p class="font-semibold text-[#22344B]">${subsidy.subsidy_name}</p>
                <p class="mt-0.5 text-[11px] text-[#5C7A99]">対象: ${subsidy.target_type} / 上限: ¥${Number(subsidy.max_amount).toLocaleString()}</p>
                <p class="text-[11px] text-[#5C7A99]">補助率: ${subsidy.rate}</p>
            </div>
        `).join('');
    } catch (err) {
        subsidyList.innerHTML = '<p class="text-rose-700 py-4 text-center text-xs">補助金情報を読み込めませんでした</p>';
    }
}

function updateChart(house) {
    const canvas = document.getElementById('financeChart');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (chartInstance) chartInstance.destroy();

    if (!house) {
        chartInstance = new Chart(ctx, {
            type: 'bar',
            data: {
                labels: ['1年目', '2年目', '3年目', '4年目', '5年目'],
                datasets: [
                    { label: '売上 (万円)', data: [0, 0, 0, 0, 0], backgroundColor: '#E0E0E0' },
                    { label: 'コスト (万円)', data: [0, 0, 0, 0, 0], backgroundColor: '#EEEEEE' }
                ]
            },
            options: { responsive: true, maintainAspectRatio: false, scales: { y: { beginAtZero: true, max: 200 } } }
        });
        return;
    }

    chartInstance = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: ['1年目', '2年目', '3年目', '4年目', '5年目'],
            datasets: [
                { label: '売上 (万円)', data: [120, 140, 160, 175, 190], backgroundColor: '#5F7350', borderRadius: 4 },
                { label: 'コスト (万円)', data: [80, 60, 55, 50, 50], backgroundColor: '#954C36', borderRadius: 4 }
            ]
        },
        options: { 
            responsive: true, 
            maintainAspectRatio: false,
            scales: {
                y: { beginAtZero: true, grid: { color: '#EFE9DD' } },
                x: { grid: { display: false } }
            }
        }
    });
}

function initAmenityLayers() {
    Object.keys(AMENITY_CONFIG).forEach(key => {
        amenityLayers[key] = L.layerGroup().addTo(map);
        amenityVisible[key] = true;
    });
}

function addAmenityLegendControl() {
    const legend = L.control({ position: 'topright' });
    legend.onAdd = function () {
        const container = L.DomUtil.create('div', 'amenity-legend');
        container.innerHTML = Object.entries(AMENITY_CONFIG).map(([key, cfg]) => `
            <label>
                <input type="checkbox" data-amenity="${key}" checked>
                <span>${cfg.symbol}</span>
                <span>${cfg.label}</span>
            </label>
        `).join('');

        L.DomEvent.disableClickPropagation(container);
        container.querySelectorAll('input[type="checkbox"]').forEach(checkbox => {
            checkbox.addEventListener('change', (e) => {
                const key = e.target.dataset.amenity;
                if (e.target.checked) map.addLayer(amenityLayers[key]);
                else map.removeLayer(amenityLayers[key]);
            });
        });
        return container;
    };
    legend.addTo(map);
}

async function updateNearbyAmenities(lat, lng) {
    const statusEl = document.getElementById('amenityStatus');
    const requestId = ++amenityRequestId;
    Object.values(amenityLayers).forEach(layer => layer.clearLayers());
    if (statusEl) statusEl.textContent = '周辺施設を検索中...';

    try {
        const elements = await fetchNearbyAmenities(lat, lng);
        if (requestId !== amenityRequestId) return;
        const counts = { school: 0, supermarket: 0, station: 0, hospital: 0 };

        elements.forEach(el => {
            const category = classifyAmenityElement(el.tags);
            if (!category) return;
            const point = getElementLatLng(el);
            if (!point) return;

            L.marker(point, { icon: AMENITY_CONFIG[category].icon })
                .bindPopup(`<b>${(el.tags && el.tags.name) || AMENITY_CONFIG[category].label}</b>`)
                .addTo(amenityLayers[category]);
            counts[category] += 1;
        });

        if (statusEl) statusEl.textContent = `半径1km: 学校${counts.school} / スーパー${counts.supermarket} / 駅${counts.station} / 病院${counts.hospital}`;
    } catch (err) {
        if (statusEl) statusEl.textContent = '周辺施設データなし';
    }
}

async function fetchNearbyAmenities(lat, lng) {
    const clauses = Object.values(AMENITY_CONFIG).map(cfg => `${cfg.query}(around:${AMENITY_RADIUS_M},${lat},${lng});`).join('\n');
    const res = await fetch('https://overpass-api.de/api/interpreter', { method: 'POST', body: `[out:json][timeout:10];(${clauses});out body;` });
    if (!res.ok) throw new Error();
    const data = await res.json();
    return data.elements || [];
}

function getElementLatLng(element) {
    if (element == null) return null;
    if (Number.isFinite(element.lat) && Number.isFinite(element.lon)) return [element.lat, element.lon];
    if (element.center && Number.isFinite(element.center.lat) && Number.isFinite(element.center.lon)) return [element.center.lat, element.center.lon];
    return null;
}

function classifyAmenityElement(tags) {
    if (!tags) return null;
    if (tags.amenity === 'school') return 'school';
    if (tags.shop === 'supermarket') return 'supermarket';
    if (tags.railway === 'station') return 'station';
    if (tags.amenity === 'hospital') return 'hospital';
    return null;
}

function downloadPDF() {
    const activeCard = document.querySelector('.prop-card.active');
    if (!activeCard) {
        alert("物件を選択してください");
        return;
    }
    window.location.href = `${API_BASE_URL}/api/generate-pdf?id=${activeCard.dataset.id}`;
}