// 섹터 순환(RRG, EINT-021): 국내 섹터 ETF를 KODEX 200과 비교해 지금 어느 섹터가 주도하고, 주도권이 어디로 옮겨 가는지
// 한눈에 보여 주는 참고 화면. 엔진이 매 거래일 계산한 값만 그린다(매수·매도 신호가 아님).
const esc = (value) => String(value ?? '').replace(/[&<>'"]/g, (char) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
}[char]));
const num = (value) => value !== null && value !== undefined && Number.isFinite(Number(value));
const signed = (value) => (num(value) ? `${Number(value) > 0 ? '+' : ''}${Number(value).toFixed(1)}%` : '-');

export const QUADRANT = {
  leading: { label: '주도', en: 'Leading', color: '#22c55e', fill: 'rgba(34,197,94,.10)', note: '시장보다 강하고 더 강해지는 중' },
  weakening: { label: '약화', en: 'Weakening', color: '#f5b800', fill: 'rgba(245,184,0,.09)', note: '아직 강하지만 힘이 빠지는 중' },
  lagging: { label: '부진', en: 'Lagging', color: '#ff4d5e', fill: 'rgba(255,77,94,.09)', note: '시장보다 약하고 더 약해지는 중' },
  improving: { label: '개선', en: 'Improving', color: '#3b8bff', fill: 'rgba(59,139,255,.10)', note: '아직 약하지만 힘이 붙는 중' },
};
const ORDER = ['leading', 'improving', 'weakening', 'lagging'];
const PALETTE = ['#f97316', '#22d3ee', '#a78bfa', '#f472b6', '#facc15', '#4ade80', '#60a5fa', '#fb7185', '#2dd4bf', '#c084fc',
  '#fbbf24', '#38bdf8', '#e879f9', '#a3e635', '#f87171', '#818cf8', '#34d399', '#fda4af', '#93c5fd'];
// 넓은 화면은 가로형, 휴대폰은 좁은 좌표계(글자가 상대적으로 커진다)로 그린다.
const SIZE = { wide: { w: 1000, h: 640 }, compact: { w: 560, h: 640 } };
const PAD = { l: 58, r: 24, t: 24, b: 46 };

export function colorOf(index) { return PALETTE[index % PALETTE.length]; }

/** 100을 가운데 두는 대칭 축 범위(모든 궤적이 들어가게, 너무 좁지 않게). */
export function rrgDomain(sectors, tail) {
  let dx = 1.2;
  let dy = 1.2;
  sectors.forEach((sector) => sector.trail.slice(-tail).forEach((point) => {
    dx = Math.max(dx, Math.abs(point.rs_ratio - 100));
    dy = Math.max(dy, Math.abs(point.rs_momentum - 100));
  }));
  return { x: [100 - dx * 1.12, 100 + dx * 1.12], y: [100 - dy * 1.12, 100 + dy * 1.12] };
}

/** 주도권 이동 요약: 지금 주도, 주도로 올라오는 중, 주도에서 밀려나는 중, 부진. */
export function rotationSummary(sectors) {
  const byQuadrant = Object.fromEntries(ORDER.map((key) => [key, sectors.filter((s) => s.quadrant === key)]));
  const rising = [...byQuadrant.improving].sort((a, b) => (b.rs_ratio + b.rs_momentum) - (a.rs_ratio + a.rs_momentum));
  const newLeaders = byQuadrant.leading.filter((s) => s.quadrant_4w_ago !== 'leading');
  const fading = [...byQuadrant.weakening].sort((a, b) => a.rs_momentum - b.rs_momentum);
  return {
    leading: [...byQuadrant.leading].sort((a, b) => b.rs_ratio - a.rs_ratio),
    rising, newLeaders, fading,
    lagging: [...byQuadrant.lagging].sort((a, b) => a.rs_ratio - b.rs_ratio),
    moves: sectors.filter((s) => s.quadrant !== s.quadrant_4w_ago),
  };
}

export function summaryHtml(data) {
  const sum = rotationSummary(data.sectors);
  const names = (list, empty = '없음') => (list.length ? list.map((s) => `<b>${esc(s.name)}</b>`).join(' · ') : `<span class="rrg-muted">${empty}</span>`);
  const headline = sum.leading.length
    ? `지금 시장을 이끄는 섹터는 ${names(sum.leading)}입니다.`
    : '지금 뚜렷하게 시장을 이끄는 섹터가 없습니다.';
  const next = sum.rising.length ? `다음 주도 후보(힘이 붙는 중): ${names(sum.rising.slice(0, 4))}` : '';
  const fade = sum.fading.length ? `주도에서 밀려나는 중: ${names(sum.fading)}` : '';
  return `<p class="rrg-headline"><i class="fa-solid fa-route"></i> ${headline} ${next ? `<br><span>${next}</span>` : ''}${fade ? `<br><span>${fade}</span>` : ''}</p>
  <div class="rrg-cards">${ORDER.map((key) => {
    const q = QUADRANT[key];
    const list = key === 'leading' ? sum.leading : key === 'improving' ? sum.rising : key === 'weakening' ? sum.fading : sum.lagging;
    return `<article class="rrg-card" style="--q:${q.color}">
      <header><b>${q.label}</b><small>${q.en} · ${esc(q.note)}</small><em>${list.length}</em></header>
      <ul>${list.map((s) => `<li><span>${esc(s.name)}</span>${s.quadrant_4w_ago !== s.quadrant
        ? `<small class="rrg-move">${esc(QUADRANT[s.quadrant_4w_ago].label)} → ${esc(q.label)}</small>` : ''}<small>${signed(s.relative_4w_pct)}</small></li>`).join('') || '<li class="rrg-muted">해당 섹터 없음</li>'}</ul>
    </article>`;
  }).join('')}</div>`;
}

/** 이름표가 겹치면 아래로 조금씩 밀어 낸다(같은 가로 범위에서 16px 간격 유지). */
export function placeLabels(items) {
  const placed = [];
  [...items].sort((a, b) => a.y - b.y).forEach((item) => {
    let y = item.y;
    placed.filter((other) => Math.abs(other.x - item.x) < (other.width + item.width) / 2).forEach((other) => {
      if (Math.abs(other.y - y) < 16) y = other.y + 16;
    });
    placed.push({ ...item, y });
  });
  return items.map((item) => placed.find((p) => p.key === item.key));
}

export function rrgSvg(data, { tail = 8, hidden = new Set(), focus = null, compact = false } = {}) {
  const { w: W, h: H } = compact ? SIZE.compact : SIZE.wide;
  const sectors = data.sectors.filter((s) => !hidden.has(s.key));
  const domain = rrgDomain(sectors.length ? sectors : data.sectors, tail);
  const sx = (v) => PAD.l + (v - domain.x[0]) / (domain.x[1] - domain.x[0]) * (W - PAD.l - PAD.r);
  const sy = (v) => PAD.t + (domain.y[1] - v) / (domain.y[1] - domain.y[0]) * (H - PAD.t - PAD.b);
  const cx = sx(100);
  const cy = sy(100);
  const quad = (key, x, y, w, h, labelX, labelY, anchor) => `<rect x="${x}" y="${y}" width="${w}" height="${h}" fill="${QUADRANT[key].fill}"/>
    <text x="${labelX}" y="${labelY}" text-anchor="${anchor}" class="rrg-quad-label" fill="${QUADRANT[key].color}">${QUADRANT[key].label} <tspan class="rrg-quad-en">${QUADRANT[key].en}</tspan></text>`;
  const ticks = (range, axis) => {
    const out = [];
    const n = compact ? 4 : 6;
    const step = (range[1] - range[0]) / n;
    for (let i = 0; i <= n; i += 1) {
      const v = range[0] + step * i;
      out.push(axis === 'x'
        ? `<text x="${sx(v)}" y="${H - PAD.b + 18}" text-anchor="middle" class="rrg-tick">${v.toFixed(1)}</text>`
        : `<text x="${PAD.l - 8}" y="${sy(v) + 4}" text-anchor="end" class="rrg-tick">${v.toFixed(1)}</text>`);
    }
    return out.join('');
  };
  const labels = placeLabels(sectors.map((sector) => {
    const head = sector.trail.slice(-tail).at(-1);
    return { key: sector.key, x: sx(head.rs_ratio), y: sy(head.rs_momentum) - 8, width: sector.name.length * 14 + 10 };
  }));
  const trails = sectors.map((sector) => {
    const index = data.sectors.indexOf(sector);
    const color = colorOf(index);
    const points = sector.trail.slice(-tail);
    const dim = focus && focus !== sector.key;
    const segs = points.slice(1).map((p, i) => {
      const a = points[i];
      const opacity = 0.25 + 0.75 * ((i + 1) / Math.max(1, points.length - 1));
      return `<line x1="${sx(a.rs_ratio)}" y1="${sy(a.rs_momentum)}" x2="${sx(p.rs_ratio)}" y2="${sy(p.rs_momentum)}" stroke="${color}" stroke-width="${focus === sector.key ? 3.2 : 2}" stroke-opacity="${opacity}"${i === points.length - 2 ? ` marker-end="url(#rrg-arrow-${index})"` : ''}/>`;
    }).join('');
    const dots = points.slice(0, -1).map((p, i) => `<circle cx="${sx(p.rs_ratio)}" cy="${sy(p.rs_momentum)}" r="2.6" fill="${color}" fill-opacity="${0.3 + 0.6 * (i / Math.max(1, points.length - 1))}"><title>${esc(sector.name)} ${esc(p.week)} · 상대강도 ${p.rs_ratio} · 모멘텀 ${p.rs_momentum}</title></circle>`).join('');
    const head = points[points.length - 1];
    const hx = sx(head.rs_ratio);
    const hy = sy(head.rs_momentum);
    const right = hx < W - PAD.r - (compact ? 110 : 90);
    const label = labels.find((item) => item.key === sector.key);
    return `<g class="rrg-sector${dim ? ' is-dim' : ''}" data-rrg-key="${esc(sector.key)}">
      <defs><marker id="rrg-arrow-${index}" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="${color}"/></marker></defs>
      ${segs}${dots}
      <circle cx="${hx}" cy="${hy}" r="6" fill="${color}" stroke="#0a101c" stroke-width="2"><title>${esc(sector.name)} (${esc(sector.etf_name)}) · ${esc(QUADRANT[sector.quadrant].label)} · 상대강도 ${head.rs_ratio} · 모멘텀 ${head.rs_momentum}</title></circle>
      ${label.y !== hy - 8 ? `<line x1="${hx}" y1="${hy}" x2="${hx + (right ? 8 : -8)}" y2="${label.y - 4}" stroke="${color}" stroke-opacity=".5"/>` : ''}
      <text x="${hx + (right ? 9 : -9)}" y="${label.y}" text-anchor="${right ? 'start' : 'end'}" class="rrg-name" fill="${color}">${esc(sector.name)}</text>
    </g>`;
  }).join('');
  return `<svg class="rrg-svg${compact ? ' is-compact' : ''}" viewBox="0 0 ${W} ${H}" role="img" aria-label="섹터 순환 그림: 가로축 상대강도, 세로축 모멘텀">
    ${quad('improving', PAD.l, PAD.t, cx - PAD.l, cy - PAD.t, PAD.l + 12, PAD.t + 24, 'start')}
    ${quad('leading', cx, PAD.t, W - PAD.r - cx, cy - PAD.t, W - PAD.r - 12, PAD.t + 24, 'end')}
    ${quad('lagging', PAD.l, cy, cx - PAD.l, H - PAD.b - cy, PAD.l + 12, H - PAD.b - 12, 'start')}
    ${quad('weakening', cx, cy, W - PAD.r - cx, H - PAD.b - cy, W - PAD.r - 12, H - PAD.b - 12, 'end')}
    <line x1="${cx}" y1="${PAD.t}" x2="${cx}" y2="${H - PAD.b}" class="rrg-axis"/>
    <line x1="${PAD.l}" y1="${cy}" x2="${W - PAD.r}" y2="${cy}" class="rrg-axis"/>
    <path d="M ${cx + 70} ${cy - 46} A 82 82 0 0 1 ${cx + 46} ${cy + 70}" class="rrg-cycle" marker-end="url(#rrg-cycle-arrow)"/>
    <path d="M ${cx - 70} ${cy + 46} A 82 82 0 0 1 ${cx - 46} ${cy - 70}" class="rrg-cycle" marker-end="url(#rrg-cycle-arrow)"/>
    <defs><marker id="rrg-cycle-arrow" viewBox="0 0 10 10" refX="7" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="#3b4a66"/></marker></defs>
    ${ticks(domain.x, 'x')}${ticks(domain.y, 'y')}
    <text x="${(PAD.l + W - PAD.r) / 2}" y="${H - 6}" text-anchor="middle" class="rrg-axis-title">${compact ? '상대강도 → 시장보다 강함' : '상대강도 (RS-Ratio) → 오른쪽일수록 시장보다 강함'}</text>
    <text x="16" y="${(PAD.t + H - PAD.b) / 2}" text-anchor="middle" class="rrg-axis-title" transform="rotate(-90 16 ${(PAD.t + H - PAD.b) / 2})">${compact ? '모멘텀 → 힘이 붙는 중' : '모멘텀 (RS-Momentum) → 위일수록 힘이 붙는 중'}</text>
    ${trails}
  </svg>`;
}

export function tableHtml(data) {
  const rows = [...data.sectors].sort((a, b) => ORDER.indexOf(a.quadrant) - ORDER.indexOf(b.quadrant) || b.rs_ratio - a.rs_ratio);
  return `<div class="efa-table-wrap"><table class="efa-table rrg-table"><thead><tr>
    <th>섹터</th><th class="rrg-opt">ETF</th><th>지금</th><th class="rrg-opt">4주 전</th><th class="is-num">상대강도</th><th class="is-num">모멘텀</th>
    <th class="is-num">4주 시장 대비</th><th class="is-num">12주 시장 대비</th><th class="is-num rrg-opt">12주 수익률</th></tr></thead><tbody>
    ${rows.map((s) => `<tr data-rrg-row="${esc(s.key)}">
      <td><b>${esc(s.name)}</b><small class="rrg-sym">${esc(s.symbol)}</small></td><td class="efa-muted rrg-opt">${esc(s.etf_name)}<small>${esc(s.symbol)}</small></td>
      <td><span class="rrg-badge" style="--q:${QUADRANT[s.quadrant].color}">${QUADRANT[s.quadrant].label}</span></td>
      <td class="rrg-opt"><span class="rrg-badge is-past" style="--q:${QUADRANT[s.quadrant_4w_ago].color}">${QUADRANT[s.quadrant_4w_ago].label}</span></td>
      <td class="is-num">${Number(s.rs_ratio).toFixed(2)}</td><td class="is-num">${Number(s.rs_momentum).toFixed(2)}</td>
      <td class="is-num ${s.relative_4w_pct > 0 ? 'is-up' : s.relative_4w_pct < 0 ? 'is-down' : ''}">${signed(s.relative_4w_pct)}</td>
      <td class="is-num ${s.relative_12w_pct > 0 ? 'is-up' : s.relative_12w_pct < 0 ? 'is-down' : ''}">${signed(s.relative_12w_pct)}</td>
      <td class="is-num rrg-opt">${signed(s.return_12w_pct)}</td></tr>`).join('')}
  </tbody></table></div>`;
}

// EINT-035: 한국(KODEX 200 대비)과 미국(SPY 대비) 섹터 순환을 같은 화면에서 전환한다.
export const MARKETS = {
  kr: { label: '한국', icon: 'fa-won-sign', sub: 'KODEX 200 대비 · 국내 섹터 ETF', url: '/api/market/sector-rotation',
    intro: '국내 섹터 ETF를 KODEX 200과 비교해, 지금 어느 섹터가 시장을 이끌고 주도권이 어디로 옮겨 가는지 보여 줍니다.' },
  us: { label: '미국', icon: 'fa-dollar-sign', sub: 'S&P 500(SPY) 대비 · SPDR 섹터 ETF', url: '/api/market/us-sector-rotation',
    intro: '미국 섹터 ETF(SPDR 11개 섹터와 반도체 SMH)를 SPY와 비교해, 지금 어느 섹터가 시장을 이끌고 주도권이 어디로 옮겨 가는지 보여 줍니다.' },
};

export function sectorRotationView(container) {
  const state = { data: null, tail: 4, hidden: new Set(), focus: null, market: 'kr' };
  try { if (localStorage.getItem('efa.rrg.market') === 'us') state.market = 'us'; } catch { /* 저장소를 못 쓰면 한국 */ }
  container.innerHTML = `<div class="efa-page rrg-page">
    <header class="efa-page-head"><div><h1><i class="fa-solid fa-arrows-spin"></i> 섹터 순환 (RRG)</h1>
      <p><span data-rrg-intro></span> 섹터는 보통 <b>개선 → 주도 → 약화 → 부진</b> 순서로 시계방향으로 돕니다.</p></div></header>
    <nav class="rrg-market-tabs" role="tablist" aria-label="시장 선택">${Object.entries(MARKETS).map(([key, m]) => `<button type="button" role="tab" data-rrg-market="${key}">
      <i class="fa-solid ${m.icon}"></i><span><b>${m.label} 섹터</b><small>${m.sub}</small></span></button>`).join('')}</nav>
    <div data-rrg-body><section class="dash-card"><p class="rrg-muted">섹터 순환 자료를 불러오는 중…</p></section></div></div>`;
  const body = container.querySelector('[data-rrg-body]');
  const narrow = window.matchMedia('(max-width: 640px)');

  // 강조는 다시 그리지 않고 class만 바꾼다(다시 그리면 마우스 아래 요소가 바뀌어 깜빡인다).
  const applyFocus = (key) => {
    state.focus = key;
    body.querySelectorAll('[data-rrg-key]').forEach((g) => g.classList.toggle('is-dim', Boolean(key) && g.dataset.rrgKey !== key));
    body.querySelectorAll('[data-rrg-key]').forEach((g) => g.classList.toggle('is-focus', g.dataset.rrgKey === key));
    body.querySelectorAll('[data-rrg-chip]').forEach((chip) => chip.classList.toggle('is-focus', chip.dataset.rrgChip === key));
    body.querySelectorAll('[data-rrg-row]').forEach((row) => row.classList.toggle('is-focus', row.dataset.rrgRow === key));
  };
  const drawChart = () => {
    const chart = body.querySelector('[data-rrg-chart]');
    chart.innerHTML = rrgSvg(state.data, { tail: state.tail, hidden: state.hidden, compact: narrow.matches });
    chart.querySelectorAll('[data-rrg-key]').forEach((g) => {
      g.addEventListener('mouseenter', () => applyFocus(g.dataset.rrgKey));
      g.addEventListener('mouseleave', () => applyFocus(null));
    });
    body.querySelectorAll('[data-rrg-chip]').forEach((chip) => chip.classList.toggle('is-off', state.hidden.has(chip.dataset.rrgChip)));
    applyFocus(state.focus);
  };

  const render = () => {
    const data = state.data;
    if (!data.available) {
      body.innerHTML = `<section class="dash-card"><div class="dash-empty"><p>${esc(data.detail || '섹터 순환 자료를 사용할 수 없습니다.')}</p></div></section>`;
      return;
    }
    body.innerHTML = `
      <section class="dash-card rrg-summary">
        <header class="dash-head"><h2><span class="rrg-market-pill is-${esc(state.market)}">${esc(MARKETS[state.market].label)}</span> 주도권 이동 요약</h2><span class="dash-sub">기준 ${esc(data.latest_week)} 주간 · 비교 대상 ${esc(data.benchmark.name)} · 최근 4주 변화${data.source ? ` · ${esc(data.source)}` : ''}</span></header>
        ${summaryHtml(data)}
      </section>
      <section class="dash-card">
        <header class="dash-head"><h2><span class="rrg-market-pill is-${esc(state.market)}">${esc(MARKETS[state.market].label)}</span> 섹터 순환 그림</h2>
          <div class="dash-seg">${[4, 8, 12].map((n) => `<button type="button" data-rrg-tail="${n}">${n}주 궤적</button>`).join('')}</div>
          <button type="button" class="dash-link" data-rrg-all>전체 보기</button></header>
        <p class="dash-note">점은 매주 위치, 화살표 끝이 이번 주입니다. 이름을 누르면 숨기거나 다시 보이고, 마우스를 올리면 그 섹터만 강조됩니다.</p>
        <div class="rrg-chips">${data.sectors.map((s, i) => `<button type="button" class="rrg-chip" data-rrg-chip="${esc(s.key)}" style="--c:${colorOf(i)}"><i></i>${esc(s.name)}</button>`).join('')}</div>
        <div class="rrg-chart" data-rrg-chart></div>
      </section>
      <section class="dash-card">
        <header class="dash-head"><h2><span class="rrg-market-pill is-${esc(state.market)}">${esc(MARKETS[state.market].label)}</span> 섹터별 현황</h2><span class="dash-sub">상대강도·모멘텀은 100이 시장 평균입니다.</span></header>
        ${tableHtml(data)}
      </section>
      <section class="dash-card rrg-guide">
        <details><summary><i class="fa-solid fa-circle-question"></i> 그림 읽는 법</summary>
          <ul>
            <li><b>가로축 상대강도</b>: 섹터 ETF ÷ ${esc(data.benchmark.name)} 비율의 최근 ${esc(data.smoothing_weeks?.short)}주 흐름이 ${esc(data.smoothing_weeks?.long)}주 흐름보다 높으면 100보다 오른쪽입니다. 요즘 시장보다 강하다는 뜻입니다.</li>
            <li><b>세로축 모멘텀</b>: 그 상대강도가 계속 높아지는 중이면 100보다 위, 꺾이기 시작하면 아래입니다.</li>
            <li><b>네 구역</b>: 개선(왼쪽 위) → 주도(오른쪽 위) → 약화(오른쪽 아래) → 부진(왼쪽 아래). 꼬리가 시계방향으로 도는 것이 보통의 순환입니다.</li>
            <li><b>주도 후보</b>: 개선 구역에서 오른쪽 위로 향하는 섹터, <b>주의</b>: 주도에서 아래로 꺾여 약화로 가는 섹터.</li>
            <li>${esc(data.disclaimer)}</li>
          </ul></details>
      </section>`;
    body.querySelectorAll('[data-rrg-tail]').forEach((button) => {
      button.classList.toggle('is-active', Number(button.dataset.rrgTail) === state.tail);
      button.addEventListener('click', () => {
        state.tail = Number(button.dataset.rrgTail);
        body.querySelectorAll('[data-rrg-tail]').forEach((b) => b.classList.toggle('is-active', b === button));
        drawChart();
      });
    });
    body.querySelectorAll('[data-rrg-chip]').forEach((chip) => {
      chip.addEventListener('click', () => {
        const key = chip.dataset.rrgChip;
        if (state.hidden.has(key)) state.hidden.delete(key); else state.hidden.add(key);
        drawChart();
      });
      chip.addEventListener('mouseenter', () => applyFocus(chip.dataset.rrgChip));
      chip.addEventListener('mouseleave', () => applyFocus(null));
    });
    body.querySelector('[data-rrg-all]').addEventListener('click', () => { state.hidden.clear(); drawChart(); });
    drawChart();
  };

  const load = () => {
    const market = MARKETS[state.market];
    container.querySelector('[data-rrg-intro]').textContent = market.intro;
    container.querySelectorAll('[data-rrg-market]').forEach((b) => { b.classList.toggle('is-active', b.dataset.rrgMarket === state.market); b.setAttribute('aria-selected', String(b.dataset.rrgMarket === state.market)); });
    body.innerHTML = '<section class="dash-card"><p class="rrg-muted">섹터 순환 자료를 불러오는 중…</p></section>';
    const requested = state.market;
    fetch(market.url)
      .then((response) => response.json())
      .then((data) => { if (requested === state.market) { state.data = data; state.hidden.clear(); state.focus = null; render(); } })
      .catch((error) => { state.data = { available: false, detail: `섹터 순환 자료를 불러오지 못했습니다: ${error.message}` }; render(); });
  };
  container.querySelectorAll('[data-rrg-market]').forEach((button) => button.addEventListener('click', () => {
    if (button.dataset.rrgMarket === state.market) return;
    state.market = button.dataset.rrgMarket;
    try { localStorage.setItem('efa.rrg.market', state.market); } catch { /* 무시 */ }
    load();
  }));
  narrow.addEventListener?.('change', () => { if (state.data?.available && body.querySelector('[data-rrg-chart]')) drawChart(); });
  load();
}
