// 평균회귀 2종의 신호 단계와 관찰 후보(EINT-033, 클로드코드·코덱스 합의 2·3단계). 추천이 아닌 관찰 정보다.
const esc = (value) => String(value ?? '').replace(/[&<>'"]/g, (char) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
}[char]));
const STAGE_LABELS = {
  stale_security: '당일 거래 없음', insufficient_history: '이력 부족(260일 미만)', low_liquidity: '거래대금 50억 미만',
  outside_market_cap_tier: '시총 순위 밖', outlier_cooldown: '최근 ±50% 급변',
};

function step(label, value, strong = false) {
  return `<li class="${strong ? 'is-key' : ''}"><span>${esc(label)}</span><b>${Number(value ?? 0).toLocaleString()}</b></li>`;
}

function watchRow(row, item) {
  const values = item === 'swing'
    ? `하단 ${row.band_gap_pct > 0 ? '+' : ''}${esc(row.band_gap_pct)}% · RSI ${esc(row.rsi)}`
    : `RSI ${esc(row.previous_rsi)}→${esc(row.rsi)} · OBV ${row.obv_rising ? '상승' : '미확인'}`;
  const missing = row.missing?.length ? `미충족: ${row.missing.map(esc).join(', ')}` : '조건 충족 · 직전 신호 진행 중';
  return `<li><strong>${esc(row.name)}</strong><small>${esc(row.symbol)} · ${esc(row.market)}</small><span>${values}</span><em>${missing}</em></li>`;
}

export function funnelHtml(data) {
  const head = '<header><h2><i class="fa-solid fa-filter"></i> 평균회귀 신호 단계 · 관찰 후보</h2><span class="sb-badge is-muted">추천 아님</span></header>';
  if (!data || !data.available) return `${head}<p class="sb-empty">${esc(data?.detail || '신호 단계 기록을 사용할 수 없습니다.')}</p>`;
  const t = data.today || {};
  const s = t.swing || {};
  const l = t.long_term || {};
  const dropped = Object.entries(t.stages || {}).map(([key, count]) => `${STAGE_LABELS[key] || key} ${Number(count).toLocaleString()}`).join(' · ');
  const recent = (data.history || []).slice(-10);
  const zeroRun = [...recent].reverse().findIndex((row) => (row.swing?.new_entry ?? 0) > 0);
  const swingZeroDays = zeroRun === -1 ? recent.length : zeroRun;
  return `${head}
    <p class="sb-note">기준일 <b>${esc(data.as_of)}</b> · 전체 ${Number(t.universe ?? 0).toLocaleString()}종목 → 기본 적격 <b>${Number(t.eligible ?? 0).toLocaleString()}</b>종목 <small>(탈락: ${esc(dropped)})</small></p>
    <div class="sb-funnel-grid">
      <article><h3>Bollinger-RSI</h3><ol class="sb-funnel-steps">
        ${step('기본 적격', s.eligible)}${step('종가 ≤ 볼린저 하단', s.below_band)}${step('RSI(14) ≤ 30', s.rsi_le_30)}
        ${step('두 조건 동시', s.both, true)}${step('새 진입 신호', s.new_entry, true)}${step('선정(추천)', s.selected, true)}
        ${step('참고: RSI ≤ 35', s.rsi_le_35)}</ol>
        ${swingZeroDays >= 3 ? `<p class="sb-note">최근 기록 ${esc(swingZeroDays)}거래일 연속 새 신호 0건 — 원인 단계는 위 숫자로 확인하세요(기준 완화는 별도 가설).</p>` : ''}
        <h4>관찰 후보 <small>${esc(data.watch_rules?.swing || '')}</small></h4>
        ${data.watch?.swing?.length ? `<ul class="sb-watch">${data.watch.swing.map((row) => watchRow(row, 'swing')).join('')}</ul>` : '<p class="sb-empty">기준 근처 종목 없음</p>'}
      </article>
      <article><h3>RSI-OBV</h3><ol class="sb-funnel-steps">
        ${step('기본 적격', l.eligible)}${step('RSI 30 상향 돌파', l.rsi_cross_30)}${step('OBV 9일 평균 위·증가', l.obv_rising)}
        ${step('두 조건 동시', l.both, true)}${step('새 진입 신호', l.new_entry, true)}${step('선정(추천)', l.selected, true)}</ol>
        <h4>관찰 후보 <small>${esc(data.watch_rules?.long_term || '')}</small></h4>
        ${data.watch?.long_term?.length ? `<ul class="sb-watch">${data.watch.long_term.map((row) => watchRow(row, 'long_term')).join('')}</ul>` : '<p class="sb-empty">기준 근처 종목 없음</p>'}
      </article>
    </div>
    <p class="sb-disclaimer">${esc(data.disclaimer)}</p>`;
}
