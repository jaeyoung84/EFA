// 추천 성과 (EINT-019): 3개 아이템 추천을 다음 거래일 시가에 샀다고 가정한 1·5·10·20거래일 사후 수익률.
// 엔진이 KRX 일봉으로 매일 계산한 파일을 보여 주기만 한다. 표본이 기준 미만이면 "판단 보류"로 표시한다.
const esc = (value) => String(value ?? '-').replace(/[&<>'"]/g, (char) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
}[char]));
const num = (value) => value !== null && value !== undefined && Number.isFinite(Number(value));
const pct = (value) => {
  if (!num(value)) return '<span class="ro-na">-</span>';
  const v = Number(value);
  return `<span class="${v > 0 ? 'ro-up' : v < 0 ? 'ro-down' : ''}">${v > 0 ? '+' : ''}${v.toFixed(2)}%</span>`;
};
const STATUS = {
  observed: '', awaiting_entry: '매수일 대기', missing_entry_bar: '매수일 시세 없음',
  entry_halted: '매수일 거래정지', missing_bar: '이후 시세 없음(상장폐지 등)',
};
const ITEM_SHORT = { swing: 'Bollinger-RSI', long_term: 'RSI-OBV', pullback_v4: 'Pullback v4' };

function itemCard(item, horizons, minSample) {
  const rows = horizons.map((h) => {
    const s = (item.horizons || {})[String(h)] || {};
    const ready = s.sample_gate === 'ready';
    return `<tr class="${ready ? '' : 'is-pending'}"><th>${h}일</th><td>${esc(s.observed_count ?? 0)}건</td>
      <td>${pct(s.mean_return_pct)}</td><td>${pct(s.median_return_pct)}</td>
      <td>${num(s.win_rate_pct) ? `${Number(s.win_rate_pct).toFixed(0)}%` : '-'}</td><td>${pct(s.mean_excess_return_pct)}</td>
      <td>${ready ? '<span class="sb-badge is-hold">판단 가능</span>' : `<span class="sb-badge is-muted">표본 부족 ${esc(s.observed_count ?? 0)}/${esc(minSample)}</span>`}</td></tr>`;
  }).join('');
  return `<article class="ro-item">
    <header><span class="sb-label">${esc(item.label)}</span><h3>${esc(item.title)}</h3>
      <span class="ro-meta">추천 ${esc(item.pick_count)}건 · ${esc(item.session_count)}일</span></header>
    <div class="sb-table"><table class="ro-table"><thead><tr><th>보유</th><th>관측</th><th>평균</th><th>중앙값</th><th>상승 비율</th><th>시장 대비</th><th>상태</th></tr></thead>
      <tbody>${rows}</tbody></table></div>
  </article>`;
}

function pickRow(row) {
  const h = (key) => {
    const value = (row.horizons || {})[key];
    return value && value.observed ? pct(value.return_pct) : '<span class="ro-na">대기</span>';
  };
  return `<tr><td>${esc(row.as_of)}</td><td>${esc(ITEM_SHORT[row.item] || row.item)} <small>${esc(row.rank)}위</small></td>
    <td><strong>${esc(row.name)}</strong><small>${esc(row.symbol)} · ${esc(row.market)}</small></td>
    <td>${num(row.entry_open) ? Number(row.entry_open).toLocaleString('ko-KR') : '-'}<small>${esc(row.entry_date || '')}</small></td>
    <td>${h('1')}</td><td>${h('5')}</td><td>${h('10')}</td><td>${h('20')}</td>
    <td class="sb-levels">${esc(STATUS[row.status] ?? row.status)}</td></tr>`;
}

export function outcomesHtml(data) {
  const head = '<h2 class="ro-title"><i class="fa-solid fa-flag-checkered"></i> 추천 성과 (전향 관측)</h2>';
  if (!data || !data.available) {
    return `${head}<p class="sb-empty">${esc(data?.detail || '추천 성과를 사용할 수 없습니다.')}</p>`;
  }
  const horizons = data.horizons || [1, 5, 10, 20];
  const rows = data.rows || [];
  return `${head}
    <p class="ro-sub">시세 기준일 <b>${esc(data.through)}</b> · 게시 ${esc(data.source_count)}일 · 추천 ${esc(data.row_count)}건.
      추천 다음 거래일 시가에 샀다고 가정하고, 그날을 1일째로 센 N번째 거래일 종가까지의 수익률입니다.</p>
    <section class="ro-grid">${(data.items || []).map((item) => itemCard(item, horizons, data.min_sample)).join('')}</section>
    <h3 class="ro-subtitle">추천별 결과 ${rows.length < data.row_count ? `<small>최근 ${rows.length}건</small>` : ''}</h3>
    ${rows.length ? `<div class="sb-table"><table class="ro-table ro-picks"><thead><tr><th>추천일</th><th>아이템</th><th>종목</th><th>가정 매수가</th>
      <th>1일</th><th>5일</th><th>10일</th><th>20일</th><th>비고</th></tr></thead><tbody>${rows.map(pickRow).join('')}</tbody></table></div>`
      : '<p class="sb-empty">아직 추적할 추천이 없습니다.</p>'}
    <p class="sb-disclaimer">${esc(data.disclaimer)}</p>`;
}
