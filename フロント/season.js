// 季節テーマ(春夏秋冬): 日付から季節を自動判定し、配色と季節の動き(花びら・蛍・落ち葉・雪)を切り替える。
// style.css の body[data-season=...] とセットで動きます。
(function () {
  // ===== 季節テーマ(春夏秋冬) =====
  // 今日の日付から季節を自動判定(3-5月=春 / 6-8月=夏 / 9-11月=秋 / 12-2月=冬)。
  const SEASONS = {
    spring: { icon: "🌸" }, summer: { icon: "🌻" }, autumn: { icon: "🍁" }, winter: { icon: "⛄" },
  };
  function currentSeasonByDate() {
    const m = new Date().getMonth() + 1;
    if (m >= 3 && m <= 5) return "spring";
    if (m >= 6 && m <= 8) return "summer";
    if (m >= 9 && m <= 11) return "autumn";
    return "winter";
  }
  let season = currentSeasonByDate();
  try { localStorage.removeItem("akiya-season"); } catch (e) {}  // 以前のボタン選択の保存値を破棄

  const canvas = document.createElement("canvas");
  canvas.id = "season-canvas";
  canvas.setAttribute("aria-hidden", "true");
  document.body.appendChild(canvas);
  const ctx = canvas.getContext("2d");
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  // 季節の演出(花びら・落ち葉・雪など)のON/OFF。色の切り替えはOFFでも残ります。
  // 初期値はON(OSの「視差効果を減らす」設定の人はOFF)。ヘッダーのボタンで切り替えた状態は保存されます。
  let fxOn = !reduceMotion;
  try {
    const savedFx = localStorage.getItem("akiya-fx");
    if (savedFx === "on") fxOn = true;
    if (savedFx === "off") fxOn = false;
  } catch (e) {}
  let rafId = 0;
  let particles = [];
  let W = 0, H = 0;

  function resizeCanvas() {
    const dpr = window.devicePixelRatio || 1;
    W = window.innerWidth; H = window.innerHeight;
    canvas.width = W * dpr; canvas.height = H * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    rebuildParticles();
  }
  function rand(a, b) { return a + Math.random() * (b - a); }

  function makeParticle(initial) {
    const p = { x: rand(0, W), y: initial ? rand(0, H) : rand(-40, -5), phase: rand(0, Math.PI * 2) };
    if (season === "spring") {            // 桜の花びら
      Object.assign(p, { size: rand(5, 9), vy: rand(0.5, 1.2), sway: rand(0.4, 1.1), rot: rand(0, 6.28), vr: rand(-0.03, 0.03) });
    } else if (season === "summer") {     // 蛍・きらめき
      Object.assign(p, { y: rand(0, H), size: rand(1.8, 3.8), vx: rand(-0.25, 0.25), vy: rand(-0.3, 0.15), speed: rand(0.01, 0.025) });
    } else if (season === "autumn") {     // 落ち葉
      const colors = ["#d9822b", "#c0502d", "#b5651d", "#e0a030", "#9c4a1a"];
      Object.assign(p, { size: rand(7, 12), vy: rand(0.5, 1.1), sway: rand(0.6, 1.4), rot: rand(0, 6.28), vr: rand(-0.04, 0.04), color: colors[Math.floor(Math.random() * colors.length)] });
    } else {                              // 雪
      Object.assign(p, { size: rand(3, 8), vy: rand(0.5, 1.4), sway: rand(0.2, 0.7) });
    }
    return p;
  }
  function rebuildParticles() {
    const n = Math.max(18, Math.min(60, Math.round((W * H) / 22000)));
    particles = Array.from({ length: n }, () => makeParticle(true));
  }

  function drawParticle(p, t) {
    ctx.save();
    ctx.translate(p.x, p.y);
    if (season === "spring") {
      ctx.rotate(p.rot);
      ctx.fillStyle = "rgba(244,165,190,0.8)";
      ctx.beginPath(); ctx.ellipse(0, 0, p.size, p.size * 0.55, 0, 0, Math.PI * 2); ctx.fill();
      ctx.fillStyle = "rgba(255,225,235,0.9)";
      ctx.beginPath(); ctx.ellipse(-p.size * 0.2, 0, p.size * 0.4, p.size * 0.2, 0, 0, Math.PI * 2); ctx.fill();
    } else if (season === "summer") {
      const a = 0.25 + 0.55 * (0.5 + 0.5 * Math.sin(t * p.speed * 60 + p.phase));
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

  let lastT = 0;
  function tick(t) {
    // 画面のリフレッシュレート(60Hz/120Hz)に関係なく同じ速さになるよう、経過時間で補正する
    const dt = lastT ? Math.min((t - lastT) / 16.667, 3) : 1;
    lastT = t;
    ctx.clearRect(0, 0, W, H);
    for (let i = 0; i < particles.length; i++) {
      const p = particles[i];
      if (season === "summer") {
        p.x += (p.vx + Math.sin(t / 900 + p.phase) * 0.3) * dt;
        p.y += p.vy * dt;
        if (p.x < -20) p.x = W + 20; if (p.x > W + 20) p.x = -20;
        if (p.y < -20) p.y = H + 20; if (p.y > H + 20) p.y = -20;
      } else {
        p.y += p.vy * dt;
        p.x += Math.sin(t / 1000 + p.phase) * p.sway * dt;
        if (p.rot !== undefined) p.rot += p.vr * dt;
        if (p.y > H + 20) particles[i] = makeParticle(false);
      }
      drawParticle(p, t / 1000);
    }
    rafId = (!document.hidden && fxOn) ? requestAnimationFrame(tick) : 0;
  }
  function startLoop() {
    if (rafId) cancelAnimationFrame(rafId);
    lastT = 0;
    rafId = requestAnimationFrame(tick);
  }
  function stopLoop() {
    if (rafId) cancelAnimationFrame(rafId);
    rafId = 0;
    ctx.clearRect(0, 0, W, H);
  }
  document.addEventListener("visibilitychange", () => { if (!document.hidden && fxOn) startLoop(); });

  // ヘッダーの「季節の演出」ボタン(index.html の #season-fx-toggle)
  const fxBtn = document.getElementById("season-fx-toggle");
  function updateFxButton() {
    if (!fxBtn) return;
    fxBtn.textContent = fxOn ? "✨ 季節の演出: ON" : "✨ 季節の演出: OFF";
    fxBtn.setAttribute("aria-pressed", String(fxOn));
    fxBtn.style.opacity = fxOn ? "1" : "0.65";
  }
  function setFx(on) {
    fxOn = on;
    try { localStorage.setItem("akiya-fx", on ? "on" : "off"); } catch (e) {}
    if (on) startLoop(); else stopLoop();
    updateFxButton();
  }
  if (fxBtn) fxBtn.addEventListener("click", () => setFx(!fxOn));
  updateFxButton();

  function applySeason(name) {
    season = name;
    document.body.setAttribute("data-season", name);
    const iconEl = document.getElementById("season-icon");
    if (iconEl) iconEl.textContent = SEASONS[name].icon;
        rebuildParticles();
    if (!fxOn) ctx.clearRect(0, 0, W, H);
  }
  window.addEventListener("resize", resizeCanvas);
  resizeCanvas();
  applySeason(season);
  if (fxOn) startLoop();
})();