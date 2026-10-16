// EINT-040 선물·수급(일별): 코스피200 선물과 코스피 현물의 투자자별 순매수, 선물 일봉·미결제약정을 최근 3개월 보여 준다.
// 자료는 서버가 장 마감 뒤 한투 Open API(KRX)에서 받아 저장한 원자료이며 추정값이 없다. 참고자료이며 매매 신호가 아니다.
const esc = (value) => String(value ?? '').replace(/[&<>'"]/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[char]));
const tone = (value) => (value > 0 ? 'is-up' : value < 0 ? 'is-down' : '');
const BUY = '#f0304a';          // 순매수(+) — 홈페이지 상승색
const SELL = '#3b82f6';         // 순매도(−) — 홈페이지 하락색
const SERIES = { futures: '#b07f00', spot: '#9b6cf0' };   // 누적선(검증: 어두운 화면에서 밝기·색각 구분 통과)

const eok = (mn) => (mn === null || mn === undefined ? null : mn / 100);              // 백만 원 → 억 원
export const fmtEok = (v) => (v === null || v === undefined ? '-' : `${v > 0 ? '+' : ''}${Math.round(v).toLocaleString('ko-KR')}억`);
export const fmtQty = (v) => (v === null || v === undefined ? '-' : `${v > 0 ? '+' : ''}${Math.round(v).toLocaleString('ko-KR')}`);
const inv = (row, side, who) => row?.[side === 'futures' ? 'futures_flows' : 'spot_flows']?.investors?.[who] || {};

/** 최근일 값과 5·20거래일 누적(외국인 선물은 계약·억, 현물은 억). 값이 없는 날은 건너뛴다. */
export function summarize(rows) {
  const withFut = rows.filter((r) => r.futures_flows);
  const last = withFut[withFut.length - 1];
  const sum = (list, side, field) => list.reduce((acc, r) => acc + (inv(r, side, 'foreign')[field] ?? 0), 0);
  const tail = (n) => withFut.slice(-n);
  const oiRow = [...rows].reverse().find((r) => r.close_snapshot?.open_interest);
  return {
    date: last?.date || null,
    kospi: last?.futures_flows?.index ?? null, kospiChange: last?.futures_flows?.index_change_pct ?? null,
    futQty: inv(last, 'futures', 'foreign').qty ?? null, futEok: eok(inv(last, 'futures', 'foreign').amount_mn),
    fut5Qty: sum(tail(5), 'futures', 'qty'), fut20Qty: sum(tail(20), 'futures', 'qty'),
    fut5Eok: eok(sum(tail(5), 'futures', 'amount_mn')), fut20Eok: eok(sum(tail(20), 'futures', 'amount_mn')),
    spotEok: eok(inv(last, 'spot', 'foreign').amount_mn), spot20Eok: eok(sum(tail(20), 'spot', 'amount_mn')),
    oi: oiRow?.close_snapshot || null, oiDate: oiRow?.date || null,
  };
}

/** 3개월 누적 순매수(억 원) — 외국인 선물·현물. 같은 단위라 한 축에 그린다. */
export function cumulative(rows) {
  let fut = 0; let spot = 0;
  return rows.filter((r) => r.futures_flows || r.spot_flows).map((r) => {
    fut += eok(inv(r, 'futures', 'foreign').amount_mn) ?? 0;
    spot += eok(inv(r, 'spot', 'foreign').amount_mn) ?? 0;
    return { date: r.date, futures: Math.round(fut), spot: Math.round(spot) };
  });
}

export function tilesHtml(s) {
  if (!s.date) return '';
  const oi = s.oi ? `<div><span>미결제약정 (${esc(s.oiDate)})</span><strong>${Math.round(s.oi.open_interest).toLocaleString('ko-KR')}계약</strong>
      <small class="${tone(s.oi.open_interest_change)}">전일 대비 ${fmtQty(s.oi.open_interest_change)} · 베이시스 ${s.oi.basis ?? '-'}</small></div>`
    : '<div><span>미결제약정</span><strong>-</strong><small>장 마감 수집분부터 쌓입니다</small></div>';
  return `<div class="xp-summary ff-tiles">
    <div><span>외국인 선물 (${esc(s.date)})</span><strong class="${tone(s.futQty)}">${fmtQty(s.futQty)}계약</strong><small>${fmtEok(s.futEok)}</small></div>
    <div><span>외국인 선물 5일 · 20일 누적</span><strong class="${tone(s.fut20Qty)}">${fmtQty(s.fut20Qty)}계약</strong><small>5일 ${fmtQty(s.fut5Qty)}계약 · 20일 ${fmtEok(s.fut20Eok)}</small></div>
    <div><span>외국인 현물 (코스피)</span><strong class="${tone(s.spotEok)}">${fmtEok(s.spotEok)}</strong><small>20일 누적 ${fmtEok(s.spot20Eok)}</small></div>
    ${oi}
  </div>`;
}

const dateCell = (d) => `<span class="ff-year">${esc(String(d).slice(0, 5))}</span>${esc(String(d).slice(5))}`;   // 모바일은 월-일만

export function tableHtml(rows) {
  const body = [...rows].reverse().map((r) => {
    const bar = r.futures_bar;
    return `<tr><td>${dateCell(r.date)}</td>
      <td class="is-num">${r.futures_flows?.index?.toLocaleString('ko-KR') ?? '-'}<small class="${tone(r.futures_flows?.index_change_pct)}">${r.futures_flows?.index_change_pct ?? ''}%</small></td>
      <td class="is-num ff-opt">${bar ? bar.close.toFixed(2) : '-'}</td>
      <td class="is-num ${tone(inv(r, 'futures', 'foreign').qty)}">${fmtQty(inv(r, 'futures', 'foreign').qty)}</td>
      <td class="is-num ff-opt ${tone(inv(r, 'futures', 'institution').qty)}">${fmtQty(inv(r, 'futures', 'institution').qty)}</td>
      <td class="is-num ff-opt ${tone(inv(r, 'futures', 'individual').qty)}">${fmtQty(inv(r, 'futures', 'individual').qty)}</td>
      <td class="is-num ff-opt ${tone(inv(r, 'futures', 'foreign').amount_mn)}">${fmtEok(eok(inv(r, 'futures', 'foreign').amount_mn))}</td>
      <td class="is-num ${tone(inv(r, 'spot', 'foreign').amount_mn)}">${fmtEok(eok(inv(r, 'spot', 'foreign').amount_mn))}</td>
      <td class="is-num ff-opt ${tone(inv(r, 'spot', 'institution').amount_mn)}">${fmtEok(eok(inv(r, 'spot', 'institution').amount_mn))}</td>
      <td class="is-num ff-opt ${tone(inv(r, 'spot', 'individual').amount_mn)}">${fmtEok(eok(inv(r, 'spot', 'individual').amount_mn))}</td>
      <td class="is-num ff-opt">${r.close_snapshot?.open_interest ? Math.round(r.close_snapshot.open_interest).toLocaleString('ko-KR') : '-'}</td></tr>`;
  }).join('');
  // 한 줄 머리글(모바일에서 열을 숨겨도 어긋나지 않게). 모바일: 날짜·코스피·외국인 선물(계약)·외국인 현물(억)만.
  return `<div class="efa-table-wrap"><table class="efa-table ff-table"><thead><tr>
    <th>날짜</th><th class="is-num">코스피</th><th class="is-num ff-opt">선물 종가</th>
    <th class="is-num">외국인 선물<small>계약</small></th><th class="is-num ff-opt">기관 선물<small>계약</small></th><th class="is-num ff-opt">개인 선물<small>계약</small></th>
    <th class="is-num ff-opt">외국인 선물<small>억 원</small></th><th class="is-num">외국인 현물<small>억 원</small></th>
    <th class="is-num ff-opt">기관 현물<small>억 원</small></th><th class="is-num ff-opt">개인 현물<small>억 원</small></th><th class="is-num ff-opt">미결제약정</th>
  </tr></thead><tbody>${body}</tbody></table></div>`;
}

const pct = (v) => (v === null || v === undefined ? '대기' : `${v > 0 ? '+' : ''}${Number(v).toFixed(2)}%`);

/** 사전등록 KR-CRASH-REBOUND-v1 급락 관측: 사건(당일 −4%·5일 −8% 이하, A는 당일 −6% 이하)과 이후 결과. */
export function crashTableHtml(events) {
  const rows = (events || []).map((e) => `<tr><td>${dateCell(e.date)}</td><td>${e.tier === 'A' ? '폭락' : '급락'}<span class="ff-opt-inline">(${esc(e.tier)})</span></td>
    <td class="is-num ff-opt">${e.kospi?.toLocaleString('ko-KR') ?? '-'}</td><td class="is-num ${tone(e.r1)}">${pct(e.r1)}</td>
    <td class="is-num ff-opt ${tone(e.r5)}">${pct(e.r5)}</td><td class="is-num ff-opt ${tone(e.foreign_futures_qty)}">${fmtQty(e.foreign_futures_qty)}</td>
    <td class="is-num ff-opt ${tone(e.foreign_spot_mn)}">${fmtEok(eok(e.foreign_spot_mn))}</td>
    <td>${e.bounce5_20 === undefined ? '대기' : e.bounce5_20 ? '예' : '아니오'}</td>
    <td class="is-num ${tone(e.f20)}">${pct(e.f20)}</td><td class="is-num ff-opt ${tone(e.f60)}">${pct(e.f60)}</td></tr>`).join('');
  return `<section class="dash-card"><header class="dash-head"><h2>급락 관측</h2><span class="dash-sub">사전등록 KR-CRASH-REBOUND-v1 · 2026-10-15부터</span></header>
    <p class="dash-note">미리 적은 예측(2000~2026 자료): 당일 −6% 이하 폭락 뒤 20거래일 안 +5% 반등 70% 이상, 20·60거래일 뒤 상승 50~70%,
      외국인 선물 순매수·순매도는 이후 상승을 구분하지 못함. 판정은 독립 구간 10개 또는 2028-10-15에. 매매 신호가 아닙니다.</p>
    ${rows ? `<div class="efa-table-wrap"><table class="efa-table ff-table"><thead><tr><th>날짜</th><th>구분</th><th class="is-num ff-opt">코스피</th>
      <th class="is-num">당일</th><th class="is-num ff-opt">5일</th><th class="is-num ff-opt">외국인 선물(계약)</th><th class="is-num ff-opt">외국인 현물</th>
      <th>20일 안<small>+5% 반등</small></th><th class="is-num">20일 뒤</th><th class="is-num ff-opt">60일 뒤</th></tr></thead><tbody>${rows}</tbody></table></div>`
    : '<p class="dash-note">관측 시작 뒤 아직 급락 사건이 없습니다.</p>'}</section>`;
}

function drawCharts(root, rows) {
  if (typeof ApexCharts === 'undefined') return;
  const flows = rows.filter((r) => r.futures_flows);
  const base = { toolbar: { show: false }, zoom: { enabled: false }, animations: { enabled: false }, background: 'transparent',
    fontFamily: 'Pretendard, -apple-system, "Malgun Gothic", sans-serif' };
  const axisX = { type: 'datetime', labels: { format: 'MM-dd', datetimeUTC: false, hideOverlappingLabels: true, style: { fontSize: '10px' } },
    axisBorder: { show: false }, axisTicks: { show: false } };
  const yLabel = { labels: { formatter: (v) => (v === null || v === undefined ? '' : `${Math.round(v).toLocaleString('ko-KR')}억`), style: { fontSize: '10px' } } };
  new ApexCharts(root.querySelector('[data-ff-daily]'), {
    chart: { ...base, type: 'bar', height: 240 },
    series: [{ name: '외국인 선물 순매수', data: flows.map((r) => ({ x: new Date(`${r.date}T00:00:00+09:00`).getTime(), y: Math.round(eok(inv(r, 'futures', 'foreign').amount_mn) ?? 0) })) }],
    plotOptions: { bar: { columnWidth: '70%', borderRadius: 2, borderRadiusApplication: 'end', colors: { ranges: [{ from: -1e12, to: -0.0001, color: SELL }, { from: 0, to: 1e12, color: BUY }] } } },
    colors: [BUY], dataLabels: { enabled: false }, legend: { show: false }, xaxis: axisX, yaxis: yLabel,
    grid: { strokeDashArray: 3 }, tooltip: { x: { format: 'yyyy-MM-dd' }, y: { formatter: (v) => fmtEok(v) } },
  }).render();
  const cum = cumulative(rows);
  new ApexCharts(root.querySelector('[data-ff-cum]'), {
    chart: { ...base, type: 'line', height: 240 },
    series: [{ name: '외국인 선물', data: cum.map((c) => ({ x: new Date(`${c.date}T00:00:00+09:00`).getTime(), y: c.futures })) },
      { name: '외국인 현물(코스피)', data: cum.map((c) => ({ x: new Date(`${c.date}T00:00:00+09:00`).getTime(), y: c.spot })) }],
    colors: [SERIES.futures, SERIES.spot], stroke: { width: 2, curve: 'straight' }, markers: { size: 0, hover: { size: 5 } },
    legend: { show: true, position: 'top', horizontalAlign: 'left', fontSize: '11px' }, dataLabels: { enabled: false },
    xaxis: axisX, yaxis: yLabel, grid: { strokeDashArray: 3 },
    annotations: { yaxis: [{ y: 0, borderColor: '#565f74', strokeDashArray: 0 }] },
    tooltip: { shared: true, x: { format: 'yyyy-MM-dd' }, y: { formatter: (v) => fmtEok(v) } },
  }).render();
}

export async function futuresFlowsView(container) {
  container.innerHTML = `<div class="efa-page ff-page">
    <header class="efa-page-head"><div><h1><i class="fa-solid fa-scale-balanced"></i> 선물·수급 (일별)</h1>
      <p>코스피200 선물과 코스피 현물의 투자자별 순매수를 최근 3개월 보관합니다. 매일 장 마감 뒤 한국투자증권 Open API(KRX 자료)에서 받은 원자료이며, 매매 신호가 아닌 참고자료입니다.</p></div></header>
    <div data-ff-body><section class="dash-card"><p class="rrg-muted">불러오는 중…</p></section></div></div>`;
  const body = container.querySelector('[data-ff-body]');
  let data;
  try {
    const res = await fetch('/api/market/futures-flows');
    if (!res.ok) throw new Error(`서버 응답 ${res.status}`);
    data = await res.json();
  } catch (error) {
    body.innerHTML = `<section class="dash-card"><p class="dash-note is-error">선물·수급 자료를 불러오지 못했습니다: ${esc(error.message)}</p></section>`;
    return;
  }
  const rows = data.rows || [];
  if (!rows.length) {
    body.innerHTML = '<section class="dash-card"><div class="dash-empty"><p>아직 저장된 자료가 없습니다. 서버가 평일 장 마감 뒤(16:20부터) 최근 3개월치를 받아 채웁니다.</p></div></section>';
    return;
  }
  const s = summarize(rows);
  const contract = [...rows].reverse().find((r) => r.bar_contract)?.bar_contract;
  const lastRun = data.last_run;
  body.innerHTML = `<section class="dash-card">${tilesHtml(s)}</section>
    <section class="dash-card"><header class="dash-head"><h2>외국인 선물 일별 순매수</h2><span class="dash-sub">억 원 · 빨강 순매수 / 파랑 순매도</span></header><div data-ff-daily></div></section>
    <section class="dash-card"><header class="dash-head"><h2>3개월 누적 순매수 — 외국인 선물 vs 현물</h2><span class="dash-sub">억 원 · 보관 기간 첫날부터 누적</span></header><div data-ff-cum></div></section>
    ${crashTableHtml(data.crash_events)}
    <section class="dash-card"><header class="dash-head"><h2>일별 표</h2><span class="dash-sub">선물 수량은 계약, 금액은 억 원</span></header>${tableHtml(rows)}</section>
    <p class="dash-note">출처 ${esc(data.source)} · 보관 ${data.retain_days}일 · 선물 종가는 ${esc(contract?.name || '현재 최근월물')} 기준(월물이 바뀌기 전 날짜도 같은 월물) ·
      미결제약정은 장 마감 뒤 수집한 날부터 기록${lastRun ? ` · 마지막 수집 ${esc(new Date(lastRun.at).toLocaleString('ko-KR', { timeZone: 'Asia/Seoul' }))} (${esc(lastRun.status)})` : ''}</p>`;
  drawCharts(body, rows);
}
