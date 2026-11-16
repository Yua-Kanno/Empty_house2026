// 「住みたい」と思ってもらうための2つの工夫
//   1. 周辺の魅力レーダーチャート(半径1km圏内の施設数をもとにした目安)
//   2. 四季の暮らしプレビュー(季節ボタンで、画面ごとその季節に切り替わる)
// script.js の updateSelection / clearDetail / updateNearbyAmenities から呼ばれます。
(function () {
  "use strict";

  // =====================================================================
  // 2. 四季の暮らしプレビュー
  // =====================================================================
  const SEASON_NAMES = { spring: "春", summer: "夏", autumn: "秋", winter: "冬" };
  const SEASON_ICONS = { spring: "🌸", summer: "🌻", autumn: "🍁", winter: "⛄" };

  // 都道府県 → 気候のおおまかな地域区分
  const REGION_OF = {};
  const REGION_GROUPS = {
    hokkaido: ["北海道"],
    tohoku: ["青森県", "岩手県", "宮城県", "秋田県", "山形県", "福島県"],
    kanto: ["茨城県", "栃木県", "群馬県", "埼玉県", "千葉県", "東京都", "神奈川県"],
    hokuriku: ["新潟県", "富山県", "石川県", "福井県", "長野県"],
    tokai: ["山梨県", "岐阜県", "静岡県", "愛知県", "三重県"],
    kansai: ["滋賀県", "京都府", "大阪府", "兵庫県", "奈良県", "和歌山県"],
    chushikoku: ["鳥取県", "島根県", "岡山県", "広島県", "山口県", "徳島県", "香川県", "愛媛県", "高知県"],
    kyushu: ["福岡県", "佐賀県", "長崎県", "熊本県", "大分県", "宮崎県", "鹿児島県"],
    okinawa: ["沖縄県"],
  };
  Object.entries(REGION_GROUPS).forEach(([region, prefs]) => prefs.forEach((p) => (REGION_OF[p] = region)));

  // 地域ごとの季節の雰囲気(一般的な傾向。個別の土地の事実ではありません)
  const REGION_SEASON = {
    hokkaido: {
      spring: "雪解けとともに遅い春が来て、庭先に少しずつ緑が戻ってくる季節です。",
      summer: "湿気が少なく、からっと過ごしやすい夏が楽しめる傾向があります。",
      autumn: "紅葉が早く、澄んだ空気の中で秋の味覚を楽しめる季節です。",
      winter: "雪景色が広がる長い冬。暖かい室内で過ごす時間が暮らしの中心になります。",
    },
    tohoku: {
      spring: "桜の開花が遅めで、ゆっくりと春が訪れる地域が多くあります。",
      summer: "朝晩は涼しく、緑の濃い風景の中でのんびり過ごせる傾向があります。",
      autumn: "山々が色づき、実りの季節を迎えます。",
      winter: "雪が積もる地域が多く、雪かきなど冬ならではの備えも必要になります。",
    },
    kanto: {
      spring: "桜が咲き、散歩やお花見が気持ちいい季節です。",
      summer: "暑さが厳しい日も多く、日陰や風通しのよい家が頼りになります。",
      autumn: "過ごしやすい日が続き、周辺の散策に向いた季節です。",
      winter: "晴れの日が多く、空気が澄んで遠くの山がきれいに見えます(山間部は雪が降ることもあります)。",
    },
    hokuriku: {
      spring: "雪解け水が流れ、山や田畑が一斉に芽吹く季節です。",
      summer: "地域によっては朝晩が涼しく、緑の中で過ごしやすい季節です。",
      autumn: "紅葉と収穫の季節。新米や果物など、実りを楽しめる土地が多くあります。",
      winter: "雪の多い地域が多く、雪景色の美しさと雪対策の両方を考えたい季節です。",
    },
    tokai: {
      spring: "比較的温暖で、花が早く咲き、外で過ごすのが気持ちいい季節です。",
      summer: "蒸し暑い日が続くので、風通しのよさが快適さを左右します。",
      autumn: "穏やかな日が多く、紅葉やお出かけにちょうどいい季節です。",
      winter: "比較的温暖な地域が多く、冬も晴れた日は外に出やすい傾向があります(山間部は冷え込みます)。",
    },
    kansai: {
      spring: "桜や新緑が楽しめ、街へも自然へも出かけやすい季節です。",
      summer: "暑さの厳しい日が多く、日陰や風通しの工夫が活きます。",
      autumn: "紅葉の名所も多く、散策にぴったりの季節です。",
      winter: "冷え込む日もありますが、日差しの暖かい日も多い季節です。",
    },
    chushikoku: {
      spring: "穏やかな気候で、花や新緑を楽しめる季節です。",
      summer: "日差しが強く暑い日もあり、暑さ対策が大切な季節です。",
      autumn: "実りの季節で、地域の食べ物が豊かになる時期です。",
      winter: "地域による差が大きく、雪が積もる所も温暖な所もあります。",
    },
    kyushu: {
      spring: "暖かくなるのが早く、花や新緑を一足早く楽しめる傾向があります。",
      summer: "暑さが厳しく、梅雨や台風への備えも大切な季節です。",
      autumn: "過ごしやすい日が続き、海や山、温泉へのお出かけに向いた季節です。",
      winter: "比較的温暖で、冬も屋外で過ごしやすい日が多くなります。",
    },
    okinawa: {
      spring: "一足早く初夏の気配が感じられ、花や緑が鮮やかな季節です。",
      summer: "日差しが強く、海や青空を満喫できる反面、暑さと台風への備えが必要です。",
      autumn: "暑さがやわらぎ、屋外で過ごしやすい日が増えてきます。",
      winter: "本州より暖かく、穏やかに過ごせる日が多い季節です。",
    },
  };

  // 想定用途ごとの「この家での過ごし方」の例
  const LIFE_BY_USAGE = {
    cafe: {
      spring: "テラス席やテイクアウトで、春の訪問客を迎えるカフェづくりを楽しめます。",
      summer: "冷たいドリンクなど、夏限定のメニューで常連さんを迎えられます。",
      autumn: "地域の旬の食材を取り入れた、秋のメニューを考えたくなります。",
      winter: "あたたかい飲み物とストーブで、ほっとできる居場所になります。",
    },
    inn: {
      spring: "春の旅行シーズン、庭や景色を楽しむ宿泊客を迎えられます。",
      summer: "夏休みの家族連れに、自然体験とあわせて提案できます。",
      autumn: "紅葉と食を目当てに訪れる旅行者を迎える季節です。",
      winter: "静かな冬の宿として、ゆっくり過ごす滞在を提案できます。",
    },
    home: {
      spring: "窓を開けて、朝の空気と鳥の声で一日が始まります。",
      summer: "縁側や庭で夕涼み。季節の野菜づくりも楽しめます。",
      autumn: "収穫や薪の準備など、季節の暮らしの手仕事が増えていきます。",
      winter: "家の中をあたたかく整えて、じっくり過ごす時間を楽しめます。",
    },
  };

  function usageKind(usage) {
    const u = String(usage || "");
    if (/カフェ|飲食|喫茶|レストラン|店/.test(u)) return "cafe";
    if (/民泊|宿|ゲストハウス|旅館/.test(u)) return "inn";
    return "home";
  }

  let currentHouse = null;
  let currentEvents = null; // null=読み込み中 / {events:[...]}=取得済み(0件のこともある)
  const eventsCache = {};

  // 自治体の年中行事(サーバーの /api/events。data/municipal_events.json の内容)
  async function loadEvents(house) {
    const muni = house.municipality;
    if (!muni) { currentEvents = { events: [] }; return; }
    const pref = typeof guessPrefecture === "function" ? guessPrefecture(house) || "" : "";
    const key = `${pref}|${muni}`;
    if (eventsCache[key]) { currentEvents = eventsCache[key]; return; }
    try {
      const base = typeof API_BASE_URL !== "undefined" ? API_BASE_URL : "";
      const res = await fetch(`${base}/api/events?municipality=${encodeURIComponent(muni)}&prefecture=${encodeURIComponent(pref)}`);
      if (!res.ok) throw new Error("events");
      const data = await res.json();
      currentEvents = { events: Array.isArray(data.events) ? data.events : [] };
    } catch (e) {
      currentEvents = { events: [] };
    }
    eventsCache[key] = currentEvents;
  }
  function safeHttpUrl(u) {
    try { const x = new URL(u); return x.protocol === "https:" || x.protocol === "http:" ? x.href : null; } catch (e) { return null; }
  }

  function currentSeason() {
    return window.AkiyaSeason ? window.AkiyaSeason.current() : "autumn";
  }

  function renderSeasonPreview() {
    const body = document.getElementById("seasonPreviewBody");
    const season = currentSeason();
    document.querySelectorAll(".season-tab").forEach((b) => {
      b.classList.toggle("active", b.dataset.season === season);
    });
    if (!body) return;
    body.replaceChildren();

    if (!currentHouse) {
      const p = document.createElement("p");
      p.className = "text-xs text-[color:var(--ink-soft)]";
      p.textContent = "物件を選ぶと、その土地での四季の暮らしがイメージできます。季節ボタンで画面の季節も変わります。";
      body.appendChild(p);
      return;
    }

    const pref = typeof guessPrefecture === "function" ? guessPrefecture(currentHouse) : null;
    const region = (pref && REGION_OF[pref]) || "kanto";
    const usage = currentHouse.usage || currentHouse.purpose || "";
    const life = LIFE_BY_USAGE[usageKind(usage)];

    const title = document.createElement("p");
    title.className = "text-sm font-bold text-[color:var(--ink)] mb-1";
    title.textContent = `${SEASON_ICONS[season]} ${SEASON_NAMES[season]}の暮らし`;
    body.appendChild(title);

    // --- 例年の行事(自治体の公式サイトの情報)---
    const heading = document.createElement("p");
    heading.className = "text-[11px] font-bold text-[color:var(--gold)] mt-1";
    heading.textContent = "📅 この町の例年の行事";
    body.appendChild(heading);
    const seasonEvents = currentEvents ? currentEvents.events.filter((e) => e.season === season) : [];
    if (currentEvents === null) {
      const p = document.createElement("p");
      p.className = "text-xs text-[color:var(--ink-soft)]";
      p.textContent = "行事の情報を読み込み中...";
      body.appendChild(p);
    } else if (seasonEvents.length === 0) {
      const p = document.createElement("p");
      p.className = "text-xs text-[color:var(--ink-soft)]";
      p.textContent = "この季節の行事の情報は、まだ集まっていません。";
      body.appendChild(p);
    } else {
      const ul = document.createElement("ul");
      ul.className = "text-xs leading-relaxed text-[color:var(--ink)] space-y-1";
      seasonEvents.forEach((ev) => {
        const li = document.createElement("li");
        const strong = document.createElement("span");
        strong.className = "font-bold";
        strong.textContent = `${ev.month}月 ${ev.name}`;
        li.appendChild(strong);
        if (ev.summary) li.appendChild(document.createTextNode(` — ${ev.summary}`));
        const href = safeHttpUrl(ev.source_url);
        if (href) {
          const a = document.createElement("a");
          a.href = href; a.target = "_blank"; a.rel = "noopener noreferrer";
          a.className = "ml-1 text-[10px] underline text-[color:var(--ink-soft)]";
          a.textContent = "[公式サイト]";
          li.appendChild(a);
        }
        ul.appendChild(li);
      });
      body.appendChild(ul);
      const evNote = document.createElement("p");
      evNote.className = "text-[10px] text-[color:var(--ink-soft)] mt-1";
      evNote.textContent = "※自治体の公式サイトに載っていた例年の行事です。日程・内容は年により変わるため、リンク先で最新情報をご確認ください。";
      body.appendChild(evNote);
    }

    // --- 気候・過ごし方のイメージ(一般的な傾向)---
    const scenery = document.createElement("p");
    scenery.className = "text-xs leading-relaxed text-[color:var(--ink)] mt-2";
    scenery.textContent = REGION_SEASON[region][season];
    const act = document.createElement("p");
    act.className = "text-xs leading-relaxed text-[color:var(--ink)] mt-1";
    act.textContent = life[season];
    const note = document.createElement("p");
    note.className = "text-[10px] text-[color:var(--ink-soft)] mt-1";
    note.textContent = "※上の2文は、地域区分ごとの一般的な傾向をもとにしたイメージです。";
    body.append(scenery, act, note);
  }

  function initSeasonPreview() {
    document.querySelectorAll(".season-tab").forEach((btn) => {
      btn.addEventListener("click", () => {
        if (window.AkiyaSeason) window.AkiyaSeason.set(btn.dataset.season);
      });
    });
    const reset = document.getElementById("seasonPreviewReset");
    if (reset) reset.addEventListener("click", () => window.AkiyaSeason && window.AkiyaSeason.reset());
    document.addEventListener("seasonchange", () => {
      renderSeasonPreview();
      redrawRadar();
    });
    renderSeasonPreview();
  }

  // =====================================================================
  // 1. 周辺の魅力レーダーチャート
  // =====================================================================
  // 「この数以上あれば満点(5点)」の基準。1km圏内の件数で判定。
  const AXES = [
    { key: "school", label: "学校", cap: 3 },
    { key: "supermarket", label: "スーパー", cap: 2 },
    { key: "station", label: "駅", cap: 1 },
    { key: "hospital", label: "病院", cap: 1 },
    { key: "nature", label: "自然・田園", cap: 6 },
    { key: "food", label: "カフェ・飲食", cap: 5 },
  ];

  let radarChart = null;
  let lastRadar = null; // { labels, scores }
  let radarToken = 0;

  function cssVar(name, fallback) {
    return getComputedStyle(document.body).getPropertyValue(name).trim() || fallback;
  }
  function hexToRgba(hex, a) {
    const m = /^#?([0-9a-f]{6})$/i.exec(hex);
    if (!m) return `rgba(176,134,42,${a})`;
    const n = parseInt(m[1], 16);
    return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
  }
  function score(count, cap) {
    return Math.round(Math.min(count / cap, 1) * 50) / 10; // 0〜5(小数1桁)
  }

  function setRadarVisible(visible) {
    const wrap = document.getElementById("appealRadarWrap");
    if (wrap) wrap.classList.toggle("hidden", !visible);
  }
  function setSummary(text) {
    const el = document.getElementById("appealSummary");
    if (el) el.textContent = text;
  }

  function summarize(axes) {
    const strong = axes.filter((a) => a.score >= 4).map((a) => a.label);
    const by = (k) => axes.find((a) => a.key === k);
    const parts = [];
    if (strong.length) parts.push(`「${strong.join("・")}」が充実したエリアです。`);
    else parts.push("施設は多くない、静かで落ち着いたエリアです。");
    const st = by("station"), sm = by("supermarket"), hp = by("hospital");
    const car = [];
    if (st && st.score === 0) car.push("駅");
    if (sm && sm.score === 0) car.push("スーパー");
    if (hp && hp.score === 0) car.push("病院");
    if (car.length) parts.push(`1km圏内に${car.join("・")}が見つからないため、車での移動を想定しておくと安心です。`);
    return parts.join("");
  }

  function drawRadar(labels, scores) {
    lastRadar = { labels, scores };
    const canvas = document.getElementById("appealRadar");
    if (!canvas || typeof Chart === "undefined") return;
    if (radarChart) radarChart.destroy();
    const gold = cssVar("--gold", "#B0862A");
    const line = cssVar("--line", "#EFE9DD");
    const ink = cssVar("--ink", "#22344B");
    radarChart = new Chart(canvas.getContext("2d"), {
      type: "radar",
      data: {
        labels,
        datasets: [{
          label: "周辺環境の目安",
          data: scores,
          backgroundColor: hexToRgba(gold, 0.25),
          borderColor: gold,
          borderWidth: 2,
          pointBackgroundColor: gold,
          pointRadius: 3,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: {
          r: {
            min: 0, max: 5,
            ticks: { display: false, stepSize: 1 },
            grid: { color: line },
            angleLines: { color: line },
            pointLabels: { color: ink, font: { size: 11 } },
          },
        },
      },
    });
  }
  function redrawRadar() {
    if (lastRadar) drawRadar(lastRadar.labels, lastRadar.scores);
  }

  // 自然・飲食の件数だけを数える(地図にはピンを出さない)
  async function fetchExtraCounts(lat, lng) {
    const R = typeof AMENITY_RADIUS_M !== "undefined" ? AMENITY_RADIUS_M : 1000;
    const a = `(around:${R},${lat},${lng})`;
    const q = `[out:json][timeout:10];` +
      `(nwr["leisure"~"^(park|garden|nature_reserve)$"]${a};nwr["natural"~"^(wood|water|beach|grassland)$"]${a};nwr["landuse"~"^(forest|meadow|orchard|farmland)$"]${a};);out count;` +
      `(nwr["amenity"~"^(cafe|restaurant)$"]${a};);out count;`;
    const res = await fetch("https://overpass-api.de/api/interpreter", { method: "POST", body: q });
    if (!res.ok) throw new Error("overpass");
    const data = await res.json();
    const totals = (data.elements || []).filter((e) => e.type === "count").map((e) => Number(e.tags && e.tags.total));
    if (totals.length < 2 || totals.some((n) => !Number.isFinite(n))) throw new Error("count");
    return { nature: totals[0], food: totals[1] };
  }

  function showAxes(counts) {
    const axes = AXES.filter((a) => counts[a.key] !== undefined).map((a) => ({ ...a, score: score(counts[a.key], a.cap) }));
    drawRadar(axes.map((a) => a.label), axes.map((a) => a.score));
    setSummary(summarize(axes));
    setRadarVisible(true);
  }

  // ---- script.js から呼ばれるフック ----
  window.onHouseSelected = function (house) {
    currentHouse = house;
    currentEvents = null;
    loadEvents(house).then(() => { if (currentHouse === house) renderSeasonPreview(); });
    renderSeasonPreview();
    radarToken++;
    lastRadar = null;
    setRadarVisible(false);
    setSummary("周辺施設を調べています...");
  };

  window.onHouseCleared = function () {
    currentHouse = null;
    currentEvents = null;
    radarToken++;
    lastRadar = null;
    if (radarChart) { radarChart.destroy(); radarChart = null; }
    setRadarVisible(false);
    setSummary("物件を選択してください");
    renderSeasonPreview();
  };

  window.onAmenityCounts = async function (counts, lat, lng) {
    const token = ++radarToken;
    if (!counts) { setSummary("周辺施設のデータを取得できませんでした"); setRadarVisible(false); return; }
    showAxes(counts); // まず4項目(学校・スーパー・駅・病院)で表示
    try {
      const extra = await fetchExtraCounts(lat, lng);
      if (token !== radarToken) return; // 別の物件に切り替わっていたら捨てる
      showAxes({ ...counts, ...extra }); // 自然・飲食を加えた6項目に更新
    } catch (e) {
      /* 追加取得に失敗しても4項目のまま表示を続ける */
    }
  };

  document.addEventListener("DOMContentLoaded", () => {
    initSeasonPreview();
    setSummary("物件を選択してください");
  });
})();