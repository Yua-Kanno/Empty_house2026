/*
 * 「ハザード情報」カードの右上に 🎒 ボタンを付けて、
 * 押すと「備えておきたいもの」のチェックリストを開閉する。
 *
 * 使い方: フロント/index.html の </body> の直前に1行足すだけ
 *   <script src="bousai-kit.js"></script>
 *
 * 画面の他のコードは変えません。カードの見出し「ハザード情報」を目印に探して付けます。
 * (カードの中身が作り直されても、自動で付け直します)
 *
 * 内容は、内閣府・各市区町村の防災の案内にある一般的な例をもとにした目安です。
 */
(function () {
  "use strict";

  var TITLE_TEXT = "ハザード情報";      // 目印にする見出しの文字
  var STORAGE_KEY = "akiya-kit-checked";

  // ---- チェックリストの内容(ここを書き換えれば、表示も変わります)----
  var LEAD = "備蓄は最低3日分、できれば1週間分が目安です。";
  var SECTIONS = [
    { title: "みんな共通", items: [
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
    ]},
    { title: "水害・高潮のおそれがあるとき", items: [
      "ビニール袋(スマホ・貴重品を水から守る)",
      "レインウェア(両手があく雨具)",
    ]},
    { title: "地震・津波のおそれがあるとき", items: [
      "ヘルメット・防災ずきん",
      "ホイッスル(閉じ込められたときの合図)",
      "枕元のスリッパ・懐中電灯",
    ]},
    { title: "土砂災害のおそれがあるとき", items: [
      "持ち出し袋を玄関に(雨が強まる前に、早めに避難できるように)",
    ]},
  ];
  var FOOT = [
    "赤ちゃん・高齢の方・ペットがいる場合は、必要なものを足してください。",
    "移住先の避難場所と避難経路は、現地で一度歩いて確認しておきましょう。",
    "市区町村・内閣府などの案内にある一般的な例をもとにした目安です。",
  ];

  // ---- チェック状態の保存(ブラウザ内だけ)----
  function loadChecked() {
    try { return JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]"); } catch (e) { return []; }
  }
  function saveChecked(list) {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(list)); } catch (e) {}
  }

  // ---- スタイル(1回だけ追加)----
  function addStyle() {
    if (document.getElementById("bousai-kit-style")) return;
    var st = document.createElement("style");
    st.id = "bousai-kit-style";
    st.textContent =
      ".bk-btn{display:inline-flex;align-items:center;justify-content:center;width:30px;height:30px;border-radius:999px;" +
      "border:1px solid var(--line,#EFE9DD);background:var(--paper,#FBF9F4);font-size:16px;line-height:1;cursor:pointer;margin-left:8px;flex-shrink:0}" +
      ".bk-btn:hover,.bk-btn[aria-expanded=true]{background:var(--sand,#F5F2EB);border-color:var(--gold,#B0862A)}" +
      ".bk-right{display:flex;align-items:center;gap:0}" +
      ".bk-panel{margin:10px 0;padding:12px 14px;border-radius:10px;background:var(--sand,#F5F2EB);color:var(--ink,#22344B);font-size:12px;line-height:1.6}" +
      ".bk-panel[hidden]{display:none}" +
      ".bk-panel h4{margin:0 0 2px;font-size:13px;font-weight:700}" +
      ".bk-panel h5{margin:10px 0 2px;font-size:12px;font-weight:700;color:var(--gold,#B0862A)}" +
      ".bk-panel ul{list-style:none;margin:0;padding:0}" +
      ".bk-panel li{margin:1px 0}" +
      ".bk-panel label{display:flex;gap:6px;align-items:flex-start;cursor:pointer}" +
      ".bk-panel input{margin-top:3px;flex-shrink:0}" +
      ".bk-panel input:checked + span{text-decoration:line-through;opacity:.55}" +
      ".bk-foot{margin-top:10px;font-size:10px;color:var(--ink-soft,#5C7A99)}";
    document.head.appendChild(st);
  }

  function buildPanel() {
    var checked = loadChecked();
    var idx = 0;
    var panel = document.createElement("div");
    panel.className = "bk-panel";
    panel.setAttribute("data-bk", "panel");
    panel.hidden = true;

    var h = document.createElement("h4");
    h.textContent = "🎒 備えておきたいもの(目安)";
    var lead = document.createElement("p");
    lead.style.margin = "0";
    lead.textContent = LEAD;
    panel.append(h, lead);

    SECTIONS.forEach(function (sec) {
      var h5 = document.createElement("h5");
      h5.textContent = sec.title;
      var ul = document.createElement("ul");
      sec.items.forEach(function (text) {
        var myIdx = idx++;
        var li = document.createElement("li");
        var label = document.createElement("label");
        var cb = document.createElement("input");
        cb.type = "checkbox";
        cb.checked = checked.indexOf(myIdx) !== -1;
        cb.addEventListener("change", function () {
          var cur = loadChecked().filter(function (n) { return n !== myIdx; });
          if (cb.checked) cur.push(myIdx);
          saveChecked(cur);
        });
        var span = document.createElement("span");
        span.textContent = text;
        label.append(cb, span);
        li.appendChild(label);
        ul.appendChild(li);
      });
      panel.append(h5, ul);
    });

    var foot = document.createElement("div");
    foot.className = "bk-foot";
    FOOT.forEach(function (t) {
      var p = document.createElement("p");
      p.style.margin = "2px 0";
      p.textContent = "※" + t;
      foot.appendChild(p);
    });
    panel.appendChild(foot);
    return panel;
  }

  // 「ハザード情報」の見出しを探す
  function findHeading() {
    var hs = document.querySelectorAll("h1,h2,h3,h4");
    for (var i = 0; i < hs.length; i++) {
      if (hs[i].textContent.indexOf(TITLE_TEXT) !== -1 && !hs[i].closest("[data-bk=panel]")) return hs[i];
    }
    return null;
  }

  // ボタンとパネルを付ける(すでにあれば何もしない)
  function ensure() {
    var h = findHeading();
    if (!h) return;
    var row = h.parentElement;                      // 見出しと、右側の「物件を選択してください」が並ぶ行
    if (!row || row.querySelector(".bk-btn")) return;

    addStyle();
    var panel = buildPanel();
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "bk-btn";
    btn.textContent = "🎒";
    btn.title = "備えておきたいもの";
    btn.setAttribute("aria-label", "備えておきたいものを見る");
    btn.setAttribute("aria-expanded", "false");
    btn.addEventListener("click", function () {
      panel.hidden = !panel.hidden;
      btn.setAttribute("aria-expanded", String(!panel.hidden));
    });

    var right = h.nextElementSibling;               // 右側の表示(あれば、ボタンと並べて右上に置く)
    if (right) {
      var wrap = document.createElement("div");
      wrap.className = "bk-right";
      right.replaceWith(wrap);
      wrap.append(right, btn);
    } else {
      row.appendChild(btn);
    }
    row.insertAdjacentElement("afterend", panel);
  }

  var timer = 0;
  function schedule() {
    if (timer) return;
    timer = setTimeout(function () { timer = 0; ensure(); }, 50);
  }

  function init() {
    ensure();
    // カードの中身が作り直されたら、付け直す
    new MutationObserver(schedule).observe(document.body, { childList: true, subtree: true });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
})();