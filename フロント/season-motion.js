/*
 * 季節のモーション(動きだけ)
 *   春=桜の花びら / 夏=蛍のような光 / 秋=落ち葉 / 冬=雪
 * 日付から季節を自動で決めます(3-5月=春 / 6-8月=夏 / 9-11月=秋 / 12-2月=冬)。
 * 画面の色やレイアウトは一切変えません。クリックも邪魔しません。
 *
 * 使い方: ページの </body> の直前に1行入れるだけ
 *   <script src="/static/season-motion.js"></script>
 * ON/OFFボタンを出したくない場合:
 *   <script src="/static/season-motion.js" data-toggle="false"></script>
 */
(function () {
  "use strict";

  // ---------- 設定(自分で変えたいときはここ) ----------
  var SPEED_AUTUMN = [0.5, 1.1];   // 落ち葉の落ちる速さ(小さいほどゆっくり)
  var SPEED_SPRING = [0.5, 1.2];   // 花びら
  var SPEED_WINTER = [0.5, 1.4];   // 雪
  var SNOW_SIZE = [3, 8];          // 雪の大きさ
  var DENSITY = 22000;             // 粒の数(大きいほど少ない)。画面面積 ÷ この値
  var MIN_COUNT = 18, MAX_COUNT = 60;
  var Z_INDEX = 2000;              // 他の要素より前に出す(クリックは素通し)

  var scriptEl = document.currentScript;
  var showToggle = !(scriptEl && scriptEl.getAttribute("data-toggle") === "false");

  function seasonByDate() {
    var m = new Date().getMonth() + 1;
    if (m >= 3 && m <= 5) return "spring";
    if (m >= 6 && m <= 8) return "summer";
    if (m >= 9 && m <= 11) return "autumn";
    return "winter";
  }
  var season = seasonByDate();

  var reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var fxOn = !reduceMotion;
  try {
    var saved = localStorage.getItem("akiya-fx");
    if (saved === "on") fxOn = true;
    if (saved === "off") fxOn = false;
  } catch (e) {}

  var canvas = document.createElement("canvas");
  canvas.setAttribute("aria-hidden", "true");
  canvas.style.cssText = "position:fixed;inset:0;width:100%;height:100%;pointer-events:none;z-index:" + Z_INDEX + ";";
  var ctx = canvas.getContext("2d");
  var W = 0, H = 0, particles = [], rafId = 0, lastT = 0;

  function rand(a, b) { return a + Math.random() * (b - a); }
  function range(r) { return rand(r[0], r[1]); }

  function make(initial) {
    var p = { x: rand(0, W), y: initial ? rand(0, H) : rand(-40, -5), phase: rand(0, Math.PI * 2) };
    if (season === "spring") {
      p.size = rand(5, 9); p.vy = range(SPEED_SPRING); p.sway = rand(0.4, 1.1); p.rot = rand(0, 6.28); p.vr = rand(-0.03, 0.03);
    } else if (season === "summer") {
      p.y = rand(0, H); p.size = rand(1.8, 3.8); p.vx = rand(-0.25, 0.25); p.vy = rand(-0.3, 0.15); p.speed = rand(0.01, 0.025);
    } else if (season === "autumn") {
      var colors = ["#d9822b", "#c0502d", "#b5651d", "#e0a030", "#9c4a1a"];
      p.size = rand(7, 12); p.vy = range(SPEED_AUTUMN); p.sway = rand(0.6, 1.4); p.rot = rand(0, 6.28); p.vr = rand(-0.04, 0.04);
      p.color = colors[Math.floor(Math.random() * colors.length)];
    } else {
      p.size = range(SNOW_SIZE); p.vy = range(SPEED_WINTER); p.sway = rand(0.2, 0.7);
    }
    return p;
  }
  function rebuild() {
    var n = Math.max(MIN_COUNT, Math.min(MAX_COUNT, Math.round((W * H) / DENSITY)));
    particles = [];
    for (var i = 0; i < n; i++) particles.push(make(true));
  }
  function resize() {
    var dpr = window.devicePixelRatio || 1;
    W = window.innerWidth; H = window.innerHeight;
    canvas.width = W * dpr; canvas.height = H * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    rebuild();
  }

  function draw(p, t) {
    ctx.save();
    ctx.translate(p.x, p.y);
    if (season === "spring") {
      ctx.rotate(p.rot);
      ctx.fillStyle = "rgba(244,165,190,0.8)";
      ctx.beginPath(); ctx.ellipse(0, 0, p.size, p.size * 0.55, 0, 0, Math.PI * 2); ctx.fill();
      ctx.fillStyle = "rgba(255,225,235,0.9)";
      ctx.beginPath(); ctx.ellipse(-p.size * 0.2, 0, p.size * 0.4, p.size * 0.2, 0, 0, Math.PI * 2); ctx.fill();
    } else if (season === "summer") {
      var a = 0.25 + 0.55 * (0.5 + 0.5 * Math.sin(t * p.speed * 60 + p.phase));
      ctx.shadowColor = "rgba(255,214,80," + a + ")"; ctx.shadowBlur = 14;
      ctx.fillStyle = "rgba(255,226,110," + a + ")";
      ctx.beginPath(); ctx.arc(0, 0, p.size, 0, Math.PI * 2); ctx.fill();
    } else if (season === "autumn") {
      ctx.rotate(p.rot);
      ctx.fillStyle = p.color; ctx.globalAlpha = 0.85;
      ctx.beginPath();
      ctx.moveTo(0, -p.size);
      ctx.quadraticCurveTo(p.size * 0.9, -p.size * 0.1, 0, p.size);
      ctx.quadraticCurveTo(-p.size * 0.9, -p.size * 0.1, 0, -p.size);
      ctx.fill();
      ctx.strokeStyle = "rgba(90,40,10,0.45)"; ctx.lineWidth = 0.8;
      ctx.beginPath(); ctx.moveTo(0, -p.size); ctx.lineTo(0, p.size); ctx.stroke();
    } else {
      ctx.fillStyle = "rgba(255,255,255,0.95)";
      ctx.strokeStyle = "rgba(130,165,205,0.55)"; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.arc(0, 0, p.size, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
    }
    ctx.restore();
  }

  function tick(t) {
    // 60Hz/120Hzの画面でも同じ速さになるよう、経過時間で補正する
    var dt = lastT ? Math.min((t - lastT) / 16.667, 3) : 1;
    lastT = t;
    ctx.clearRect(0, 0, W, H);
    for (var i = 0; i < particles.length; i++) {
      var p = particles[i];
      if (season === "summer") {
        p.x += (p.vx + Math.sin(t / 900 + p.phase) * 0.3) * dt;
        p.y += p.vy * dt;
        if (p.x < -20) p.x = W + 20; if (p.x > W + 20) p.x = -20;
        if (p.y < -20) p.y = H + 20; if (p.y > H + 20) p.y = -20;
      } else {
        p.y += p.vy * dt;
        p.x += Math.sin(t / 1000 + p.phase) * p.sway * dt;
        if (p.rot !== undefined) p.rot += p.vr * dt;
        if (p.y > H + 20) particles[i] = make(false);
      }
      draw(p, t / 1000);
    }
    rafId = (!document.hidden && fxOn) ? requestAnimationFrame(tick) : 0;
  }
  function start() { if (rafId) cancelAnimationFrame(rafId); lastT = 0; rafId = requestAnimationFrame(tick); }
  function stop() { if (rafId) cancelAnimationFrame(rafId); rafId = 0; ctx.clearRect(0, 0, W, H); }

  // ---------- ON/OFFボタン(ヘッダーのナビに入れる。無ければ左下に小さく表示) ----------
  var btn = null;
  function label() {
    if (!btn) return;
    btn.textContent = fxOn ? "✨ 季節の演出: ON" : "✨ 季節の演出: OFF";
    btn.setAttribute("aria-pressed", String(fxOn));
    btn.style.opacity = fxOn ? "1" : "0.65";
  }
  function setFx(on) {
    fxOn = on;
    try { localStorage.setItem("akiya-fx", on ? "on" : "off"); } catch (e) {}
    if (on) start(); else stop();
    label();
  }
  function addToggle() {
    btn = document.createElement("button");
    btn.type = "button";
    var nav = document.querySelector(".header-nav");
    if (nav) {
      // このページ全体の button 用スタイル(緑・高さ46px)を打ち消して、隣の nav-link と同じ見た目にする
      var st = document.createElement("style");
      st.textContent =
        ".akiya-fx-btn.nav-link{height:auto;line-height:normal;cursor:pointer;font-family:inherit;}" +
        ".akiya-fx-btn.nav-link:hover:not(:disabled){background:var(--accent-soft);color:var(--accent-deep);border-color:var(--accent);}";
      document.head.appendChild(st);
      btn.className = "nav-link akiya-fx-btn";
      nav.insertBefore(btn, nav.firstChild && nav.firstChild.nextSibling ? nav.firstChild.nextSibling : null);
    } else {
      btn.style.cssText = "position:fixed;left:10px;bottom:10px;z-index:" + (Z_INDEX + 1) +
        ";font-size:11px;padding:4px 10px;border-radius:999px;border:1px solid rgba(0,0,0,.15);background:rgba(255,255,255,.9);cursor:pointer;";
      document.body.appendChild(btn);
    }
    btn.addEventListener("click", function () { setFx(!fxOn); });
    label();
  }

  function init() {
    document.body.appendChild(canvas);
    window.addEventListener("resize", resize);
    document.addEventListener("visibilitychange", function () { if (!document.hidden && fxOn) start(); });
    resize();
    if (showToggle) addToggle();
    if (fxOn) start();
  }
  if (document.body) init(); else document.addEventListener("DOMContentLoaded", init);
})();