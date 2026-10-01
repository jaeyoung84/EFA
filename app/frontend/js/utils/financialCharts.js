/**
 * Shared financial chart rendering utilities (Canvas 2D).
 * Used by companyFinancial.js and dartCompanySearch.js.
 */

export function fmtNum(v) {
  if (v == null) return '-';
  const abs = Math.abs(v);
  if (abs >= 1e12) return (v / 1e12).toFixed(2) + 'T';
  if (abs >= 1e9)  return (v / 1e9).toFixed(2) + 'B';
  if (abs >= 1e6)  return (v / 1e6).toFixed(2) + 'M';
  if (abs >= 1e3)  return (v / 1e3).toFixed(1) + 'K';
  return v.toFixed(2);
}

export function fmtPct(v) {
  return v == null ? '-' : v.toFixed(1) + '%';
}

const DPR = window.devicePixelRatio || 1;

function setupCanvas(canvas) {
  // 카드 안쪽(패딩 제외) 폭에 맞춘다. 패딩까지 넣으면 캔버스가 카드 밖으로 넘친다.
  const parent = canvas.parentElement;
  let w = 400;
  if (parent && parent.clientWidth) {
    const style = getComputedStyle(parent);
    w = Math.max(240, parent.clientWidth - parseFloat(style.paddingLeft || 0) - parseFloat(style.paddingRight || 0));
  }
  const h = parseInt(canvas.getAttribute('height') || '220', 10);
  canvas.width  = w * DPR;
  canvas.height = h * DPR;
  canvas.style.width  = w + 'px';
  canvas.style.height = h + 'px';
  const ctx = canvas.getContext('2d');
  ctx.scale(DPR, DPR);
  return { ctx, W: w, H: h };
}

/** 겹치지 않을 만큼만 x축 라벨을 그린다(좁으면 n개마다 하나, 마지막 라벨은 항상). */
export function labelStep(labelWidth, slotWidth, gap = 8) {
  return slotWidth > 0 ? Math.max(1, Math.ceil((labelWidth + gap) / slotWidth)) : 1;
}

function drawXLabels(ctx, labels, xOf, y, slotWidth) {
  const widest = Math.max(0, ...labels.map((label) => ctx.measureText(String(label)).width));
  const step = labelStep(widest, slotWidth);
  const shown = labels.map((_, i) => i % step === 0);
  // 마지막 라벨은 앞에 그린 라벨과 충분히 떨어져 있을 때만 추가한다.
  const lastShown = shown.lastIndexOf(true);
  if (lastShown !== labels.length - 1 && labels.length - 1 - lastShown >= step) shown[labels.length - 1] = true;
  labels.forEach((label, i) => { if (shown[i]) ctx.fillText(label, xOf(i), y); });
}

function noData(ctx, W, H, msg = '데이터 없음') {
  ctx.clearRect(0, 0, W, H);
  ctx.fillStyle = '#94a3b8';
  ctx.font = '13px Pretendard, sans-serif';
  ctx.textAlign = 'center';
  ctx.fillText(msg, W / 2, H / 2);
}

function drawGrid(ctx, PAD, cW, cH) {
  ctx.strokeStyle = '#1e2b42';
  ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i++) {
    const y = PAD.t + (cH / 4) * i;
    ctx.beginPath(); ctx.moveTo(PAD.l, y); ctx.lineTo(PAD.l + cW, y); ctx.stroke();
  }
}

function drawYLabels(ctx, PAD, cH, minV, maxV, color = '#8a97ad', right = false, fmt = fmtNum) {
  ctx.fillStyle = color;
  ctx.font = '10px Pretendard, sans-serif';
  const range = maxV - minV || 1;
  for (let i = 0; i <= 4; i++) {
    const v = minV + (range / 4) * (4 - i);
    const y = PAD.t + (cH / 4) * i;
    if (right) {
      ctx.textAlign = 'left';
      ctx.fillText(fmt(v), PAD.l + ctx.canvas.width / DPR - PAD.r + 4, y + 4);
    } else {
      ctx.textAlign = 'right';
      ctx.fillText(fmt(v), PAD.l - 4, y + 4);
    }
  }
}

// ─── 성과 차트 ───────────────────────────────────────────────────────────────
export function drawPerformanceChart(canvas, data) {
  const { ctx, W, H } = setupCanvas(canvas);
  ctx.clearRect(0, 0, W, H);

  const rev = data.revenue        || [];
  const ni  = data.net_income     || [];
  const mgn = data.net_margin_pct || [];
  if (!rev.length) return noData(ctx, W, H);

  const PAD = { t: 24, r: 52, b: 36, l: 56 };
  const cW = W - PAD.l - PAD.r;
  const cH = H - PAD.t - PAD.b;
  const n  = rev.length;
  const barW  = cW / n;
  const grpW  = barW * 0.72;

  const revVals = rev.map(d => d.value ?? 0);
  const niVals  = ni.map(d => d.value ?? 0);
  const allVals = [...revVals, ...niVals];
  const maxV = Math.max(...allVals) * 1.15 || 1;
  const minV = Math.min(0, ...allVals);

  const mgnVals = mgn.map(d => d.value ?? 0);
  const maxM = Math.max(10, ...mgnVals) * 1.3;
  const minM = Math.min(0, ...mgnVals);

  const scaleY = v => PAD.t + cH - ((v - minV) / (maxV - minV || 1)) * cH;
  const scaleM = v => PAD.t + cH - ((v - minM) / (maxM - minM || 1)) * cH;

  drawGrid(ctx, PAD, cW, cH);

  // Revenue bars
  revVals.forEach((v, i) => {
    const x  = PAD.l + i * barW + (barW - grpW) / 2;
    const bW = grpW / 2 - 2;
    const y0 = scaleY(0), y1 = scaleY(v);
    ctx.fillStyle = '#2563eb';
    ctx.fillRect(x, Math.min(y0, y1), bW, Math.abs(y0 - y1) || 2);
  });

  // Net income bars
  niVals.forEach((v, i) => {
    const x  = PAD.l + i * barW + (barW - grpW) / 2 + grpW / 2 + 2;
    const bW = grpW / 2 - 2;
    const y0 = scaleY(0), y1 = scaleY(v);
    ctx.fillStyle = '#10b981';
    ctx.fillRect(x, Math.min(y0, y1), bW, Math.abs(y0 - y1) || 2);
  });

  // Margin line
  if (mgnVals.length > 1) {
    ctx.strokeStyle = '#f97316'; ctx.lineWidth = 2;
    ctx.beginPath();
    mgnVals.forEach((v, i) => {
      const x = PAD.l + i * barW + barW / 2;
      i === 0 ? ctx.moveTo(x, scaleM(v)) : ctx.lineTo(x, scaleM(v));
    });
    ctx.stroke();
    ctx.fillStyle = '#f97316';
    mgnVals.forEach((v, i) => {
      ctx.beginPath(); ctx.arc(PAD.l + i * barW + barW / 2, scaleM(v), 3, 0, Math.PI * 2); ctx.fill();
    });
  }

  // X labels
  ctx.fillStyle = '#8a97ad'; ctx.font = '10px Pretendard, sans-serif'; ctx.textAlign = 'center';
  drawXLabels(ctx, rev.map((d) => d.period), (i) => PAD.l + i * barW + barW / 2, H - 8, barW);

  drawYLabels(ctx, PAD, cH, minV, maxV);
  // Right axis for margin
  ctx.fillStyle = '#f97316'; ctx.textAlign = 'left';
  for (let i = 0; i <= 4; i++) {
    const v = minM + ((maxM - minM) / 4) * (4 - i);
    ctx.fillText(fmtPct(v), W - PAD.r + 4, PAD.t + (cH / 4) * i + 4);
  }
}

// ─── 워터폴 차트 ─────────────────────────────────────────────────────────────
export function drawWaterfallChart(canvas, wf) {
  const { ctx, W, H } = setupCanvas(canvas);
  ctx.clearRect(0, 0, W, H);

  const steps = [
    { key: 'revenue',            label: '수입',             type: 'absolute' },
    { key: 'cogs',               label: 'COGS',             type: 'decrease' },
    { key: 'gross_profit',       label: '총수익',           type: 'subtotal' },
    { key: 'operating_expense',  label: '운영 비용',        type: 'decrease' },
    { key: 'operating_income',   label: '영업 이익',        type: 'subtotal' },
    { key: 'other_income',       label: '영업외\n수입/지출', type: 'delta'    },
    { key: 'tax',                label: '세금 및\n기타',    type: 'decrease' },
    { key: 'net_income',         label: '순이익',           type: 'subtotal' },
  ];

  if (!steps.some(s => wf[s.key] != null)) return noData(ctx, W, H);

  const PAD = { t: 20, r: 12, b: 48, l: 56 };
  const cW  = W - PAD.l - PAD.r;
  const cH  = H - PAD.t - PAD.b;
  const bW  = (cW / steps.length) * 0.65;

  let running = 0;
  const bars = steps.map(s => {
    const val = wf[s.key] ?? 0;
    let start = 0, end = 0;
    if (s.type === 'absolute') { start = 0;       end = Math.abs(val); running = end; }
    else if (s.type === 'decrease') { start = running - Math.abs(val); end = running; running = start; }
    else if (s.type === 'delta')    { start = running; end = running + val; running = end; }
    else                            { start = 0;       end = running; }
    return { ...s, val, start, end };
  });

  const allY = bars.flatMap(b => [b.start, b.end]);
  const maxV = Math.max(...allY) * 1.1 || 1;
  const minV = Math.min(0, ...allY);
  const range = maxV - minV || 1;
  const slotW = cW / bars.length;
  const scaleY = v => PAD.t + cH - ((v - minV) / range) * cH;

  drawGrid(ctx, PAD, cW, cH);

  const y0 = scaleY(0);
  ctx.strokeStyle = '#9ca3af'; ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(PAD.l, y0); ctx.lineTo(PAD.l + cW, y0); ctx.stroke();

  // x축 라벨은 모든 막대에 같은 간격을 써서 고르게 건너뛴다.
  ctx.font = '9px Pretendard, sans-serif';
  const labelEvery = labelStep(Math.max(0, ...bars.flatMap((b) => b.label.split('\n')).map((line) => ctx.measureText(line).width)), slotW, 4);
  bars.forEach((b, i) => {
    const x    = PAD.l + i * slotW + (slotW - bW) / 2;
    const yTop = scaleY(Math.max(b.start, b.end));
    const yBot = scaleY(Math.min(b.start, b.end));
    const bH   = Math.abs(yTop - yBot) || 2;

    ctx.fillStyle = b.type === 'decrease' ? '#f43f5e'
                  : b.type === 'absolute' ? '#10b981'
                  : b.end >= 0            ? '#10b981'
                  :                         '#f43f5e';
    ctx.fillRect(x, yTop, bW, bH);

    // Value label
    ctx.fillStyle = '#c3cbd8'; ctx.font = '9px Pretendard, sans-serif'; ctx.textAlign = 'center';
    const valueText = fmtNum(Math.abs(b.val) || Math.abs(b.end));
    if (ctx.measureText(valueText).width <= slotW - 2) ctx.fillText(valueText, x + bW / 2, yTop - 3);

    // Connector
    if (i < bars.length - 1) {
      ctx.strokeStyle = '#2c3d5c'; ctx.lineWidth = 1; ctx.setLineDash([3, 3]);
      const connY  = scaleY(b.type === 'decrease' ? b.start : b.end);
      const nextX  = PAD.l + (i + 1) * slotW + (slotW - bW) / 2;
      ctx.beginPath(); ctx.moveTo(x + bW, connY); ctx.lineTo(nextX, connY); ctx.stroke();
      ctx.setLineDash([]);
    }

    // X label (multi-line)
    ctx.fillStyle = '#8a97ad'; ctx.font = '9px Pretendard, sans-serif'; ctx.textAlign = 'center';
    // 칸이 좁으면 라벨을 한 칸씩 건너뛰어 겹치지 않게 한다.
    const lines = b.label.split('\n');
    if (i % labelEvery === 0) lines.forEach((line, li) => ctx.fillText(line, x + bW / 2, H - 30 + li * 12));
  });

  drawYLabels(ctx, PAD, cH, minV, maxV);
}

// ─── 부채 차트 ───────────────────────────────────────────────────────────────
export function drawDebtChart(canvas, data) {
  const { ctx, W, H } = setupCanvas(canvas);
  ctx.clearRect(0, 0, W, H);

  const debt = data.total_debt || [];
  const fcf  = data.fcf        || [];
  const cash = data.cash       || [];
  if (!debt.length && !fcf.length && !cash.length) return noData(ctx, W, H);

  const labels = (debt.length ? debt : fcf.length ? fcf : cash).map(d => d.period);
  const n = labels.length;
  const PAD = { t: 24, r: 12, b: 36, l: 56 };
  const cW  = W - PAD.l - PAD.r;
  const cH  = H - PAD.t - PAD.b;
  const barW = cW / n;
  const grpW = barW * 0.78;

  const dVals = debt.map(d => d.value ?? 0);
  const fVals = fcf.map(d => d.value ?? 0);
  const cVals = cash.map(d => d.value ?? 0);
  const allVals = [...dVals, ...fVals, ...cVals];
  const maxV = Math.max(0, ...allVals) * 1.15 || 1;
  const minV = Math.min(0, ...allVals) * 1.15;
  const range = maxV - minV || 1;
  const scaleY = v => PAD.t + cH - ((v - minV) / range) * cH;

  drawGrid(ctx, PAD, cW, cH);
  const y0 = scaleY(0);
  ctx.strokeStyle = '#9ca3af'; ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(PAD.l, y0); ctx.lineTo(PAD.l + cW, y0); ctx.stroke();

  const series  = [dVals, fVals, cVals];
  const colors  = ['#f43f5e', '#10b981', '#2563eb'];
  const numS    = series.filter(s => s.length > 0).length || 1;
  const slotW   = grpW / numS;

  series.forEach((vals, si) => {
    if (!vals.length) return;
    vals.forEach((v, i) => {
      const x  = PAD.l + i * barW + (barW - grpW) / 2 + si * slotW;
      const yv = scaleY(v);
      ctx.fillStyle = colors[si];
      ctx.fillRect(x, Math.min(y0, yv), slotW - 2, Math.abs(y0 - yv) || 2);
    });
  });

  ctx.fillStyle = '#8a97ad'; ctx.font = '10px Pretendard, sans-serif'; ctx.textAlign = 'center';
  drawXLabels(ctx, labels, (i) => PAD.l + i * barW + barW / 2, H - 8, barW);
  drawYLabels(ctx, PAD, cH, minV, maxV);
}

// ─── 어닝 차트 ───────────────────────────────────────────────────────────────
export function drawEarningsChart(canvas, earnings) {
  const { ctx, W, H } = setupCanvas(canvas);
  ctx.clearRect(0, 0, W, H);

  const data = (earnings || []).filter(e => e.eps_estimate != null || e.eps_actual != null);
  if (!data.length) return noData(ctx, W, H, '어닝 데이터 없음');

  const PAD = { t: 20, r: 20, b: 36, l: 52 };
  const cW  = W - PAD.l - PAD.r;
  const cH  = H - PAD.t - PAD.b;
  const n   = data.length;

  const allEps = data.flatMap(e => [e.eps_estimate, e.eps_actual]).filter(v => v != null);
  const maxE   = Math.max(...allEps) * 1.2 || 1;
  const minE   = Math.min(0, ...allEps) * 1.2;
  const range  = maxE - minE || 1;
  const slotW  = cW / n;
  const scaleY = v => PAD.t + cH - ((v - minE) / range) * cH;
  const R      = Math.max(5, Math.min(10, slotW / 2.5));

  drawGrid(ctx, PAD, cW, cH);
  const y0 = scaleY(0);
  ctx.strokeStyle = '#9ca3af'; ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(PAD.l, y0); ctx.lineTo(PAD.l + cW, y0); ctx.stroke();

  data.forEach((e, i) => {
    const cx = PAD.l + i * slotW + slotW / 2;
    if (e.eps_estimate != null) {
      ctx.beginPath(); ctx.arc(cx, scaleY(e.eps_estimate), R, 0, Math.PI * 2);
      ctx.strokeStyle = '#9ca3af'; ctx.lineWidth = 1.5; ctx.stroke();
    }
    if (e.eps_actual != null) {
      const beat = e.eps_estimate != null && e.eps_actual >= e.eps_estimate;
      ctx.beginPath(); ctx.arc(cx, scaleY(e.eps_actual), R, 0, Math.PI * 2);
      ctx.fillStyle = beat ? '#10b981' : '#f43f5e'; ctx.fill();
    }
  });

  ctx.fillStyle = '#8a97ad'; ctx.font = '9px Pretendard, sans-serif'; ctx.textAlign = 'center';
  drawXLabels(ctx, data.map((e) => e.date.slice(0, 7)), (i) => PAD.l + i * slotW + slotW / 2, H - 8, slotW);

  drawYLabels(ctx, PAD, cH, minE, maxE, '#8a97ad', false, v => v.toFixed(2));
}

// ─── 범례 HTML helper ─────────────────────────────────────────────────────────
export function legendDot(color, label, isLine = false) {
  if (isLine) {
    return `<span style="display:inline-flex;align-items:center;gap:4px;font-size:0.7rem;color:#8a97ad;margin-right:8px;">
      <span style="display:inline-block;width:18px;height:2px;background:${color};border-radius:1px;"></span>${label}</span>`;
  }
  return `<span style="display:inline-flex;align-items:center;gap:4px;font-size:0.7rem;color:#8a97ad;margin-right:8px;">
    <span style="display:inline-block;width:9px;height:9px;background:${color};border-radius:2px;"></span>${label}</span>`;
}

// ─── 공용 차트 그리드 렌더러 ──────────────────────────────────────────────────
export function renderFinancialDashboard(container, data) {
  container.innerHTML = `
    <div class="fin-grid">
      <div class="fin-card">
        <div class="fin-card-hdr">
          <span class="fin-card-title">성과</span>
          <div>${legendDot('#2563eb','매출')}${legendDot('#10b981','순이익')}${legendDot('#f97316','순마진 %',true)}</div>
        </div>
        <canvas id="fcv-perf" height="200" style="width:100%;display:block;"></canvas>
      </div>
      <div class="fin-card">
        <div class="fin-card-hdr">
          <span class="fin-card-title">매출 대비 수익 전환</span>
          <div>${legendDot('#10b981','수익')}${legendDot('#f43f5e','비용')}</div>
        </div>
        <canvas id="fcv-wf" height="200" style="width:100%;display:block;"></canvas>
      </div>
      <div class="fin-card">
        <div class="fin-card-hdr">
          <span class="fin-card-title">부채 수준 및 범위</span>
          <div>${legendDot('#f43f5e','부채')}${legendDot('#10b981','잉여현금흐름')}${legendDot('#2563eb','현금')}</div>
        </div>
        <canvas id="fcv-debt" height="200" style="width:100%;display:block;"></canvas>
      </div>
      <div class="fin-card">
        <div class="fin-card-hdr">
          <span class="fin-card-title">어닝</span>
          <div>${legendDot('#10b981','발표(beat)')}${legendDot('#f43f5e','발표(miss)')}
            <span style="display:inline-flex;align-items:center;gap:4px;font-size:0.7rem;color:#8a97ad;">
              <span style="width:9px;height:9px;border-radius:50%;border:1.5px solid #9ca3af;display:inline-block;"></span>평가</span>
          </div>
        </div>
        <canvas id="fcv-earn" height="200" style="width:100%;display:block;"></canvas>
      </div>
    </div>
  `;

  if (!document.getElementById('fin-grid-styles')) {
    const s = document.createElement('style');
    s.id = 'fin-grid-styles';
    s.textContent = `
      /* 화면 폭이 아니라 담는 영역 폭에 맞춰 2열 → 1열로 바뀐다(좁은 카드 안에서도 차트가 찌그러지지 않게). */
      .fin-grid { display:grid; grid-template-columns:repeat(auto-fit, minmax(min(100%, max(380px, 45%)), 1fr)); gap:14px; margin-top:16px; }
      .fin-card { min-width:0; overflow:hidden; background:var(--surface-soft); border:1px solid var(--border); border-radius:10px; padding:14px 14px 10px; }
      .fin-card-hdr { display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:4px; margin-bottom:10px; color:var(--text-muted); font-size:.75rem; }
      .fin-card-title { font-size:0.88rem; font-weight:700; color:var(--text); }
    `;
    document.head.appendChild(s);
  }

  const drawAll = () => {
    if (!container.isConnected) return;
    drawPerformanceChart(container.querySelector('#fcv-perf'),  data.performance);
    drawWaterfallChart(container.querySelector('#fcv-wf'),     data.waterfall);
    drawDebtChart(container.querySelector('#fcv-debt'),        data.debt);
    drawEarningsChart(container.querySelector('#fcv-earn'),    data.earnings);
  };
  requestAnimationFrame(drawAll);
  // 창 크기·레이아웃이 바뀌면 새 폭으로 다시 그린다(캔버스는 그릴 때 폭이 고정되므로).
  container._finResize?.disconnect();
  if (typeof ResizeObserver !== 'undefined') {
    let lastWidth = 0;
    let frame = 0;
    container._finResize = new ResizeObserver((entries) => {
      const width = Math.round(entries[0].contentRect.width);
      if (!width || width === lastWidth) return;
      lastWidth = width;
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(drawAll);
    });
    container._finResize.observe(container);
  }
}
