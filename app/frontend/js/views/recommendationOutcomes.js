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

// 사전등록 B-1(EINT-032): 볼린저 RSI 신호를 '반등 확인 후 진입'했다면? 원래 규칙과 짝 비교한 전향 관측. 판정 전엔 기술 통계만.
const SHADOW_STATUS = {
  awaiting_s1: '다음 거래일 대기', awaiting_s2: '확인 후 진입일 대기', open: '보유 중', closed: '청산',
  not_entered: '확인 안 됨(미진입)', missing_bar: '시세 없음', entry_halted: '매수일 거래정지',
  closed_pair: '비교 완료', open_pair: '진행 중', excluded: '제외(시세 없음·정지)',
};
const VERDICT = { not_due: ['판정 전', 'is-muted'], PASS: ['PASS · 규칙 변경 제안 가능', 'is-hold'],
  FAIL: ['FAIL · 가설 종료', 'is-muted'], INCONCLUSIVE: ['판단 불가 · 채택 안 함', 'is-muted'] };

function armCell(arm) {
  if (!arm) return '<span class="ro-na">-</span>';
  if (arm.status === 'closed' || arm.status === 'not_entered') return pct(arm.net_return_pct);
  return `<span class="ro-na">${esc(SHADOW_STATUS[arm.status] || arm.status)}</span>`;
}

export function reboundShadowHtml(data) {
  const head = '<h3 class="ro-subtitle"><i class="fa-solid fa-flask"></i> 사전등록 관측 B-1 · 반등 확인 후 진입 (볼린저 RSI)</h3>';
  if (!data || !data.available) return `${head}<p class="sb-empty">${esc(data?.detail || '관측 기록을 사용할 수 없습니다.')}</p>`;
  const s = data.summary || {};
  const [verdict, tone] = VERDICT[s.verdict] || [s.verdict, 'is-muted'];
  const rows = data.rows || [];
  return `${head}
    <p class="ro-sub">${esc(data.registered_from)} 이후 볼린저 RSI 신호마다 <b>A: 다음 날 시가 매수(현재 규칙)</b>와
      <b>B: 다음 날 종가가 볼린저 하단 위·신호일 종가 위로 반등하면 그다음 날 시가 매수</b>를 비교합니다. 청산은 둘 다 현재 규칙,
      비용(수수료·거래세 0.20%·슬리피지) 반영. 시세 기준일 ${esc(data.through)}.</p>
    <div class="sb-table"><table class="ro-table"><thead><tr><th>신호</th><th>비교 완료</th><th>B 진입률</th><th>A 평균</th><th>B 평균(진입분)</th><th>평균 차이(B−A)</th><th>판정</th></tr></thead>
      <tbody><tr><td>${esc(s.signals ?? 0)}건</td><td>${esc(s.closed_pairs ?? 0)} / ${esc(s.target_closed_pairs ?? 100)}</td>
        <td>${num(s.b_entry_rate_pct) ? `${Number(s.b_entry_rate_pct).toFixed(0)}%` : '-'}</td><td>${pct(s.mean_net_a_pct)}</td>
        <td>${pct(s.mean_net_b_entered_pct)}</td><td>${pct(s.mean_diff_pct)}</td>
        <td><span class="sb-badge ${tone}">${esc(verdict)}</span>${s.ci95 ? ` <small>95% 구간 ${pct(s.ci95[0])} ~ ${pct(s.ci95[1])}</small>` : ''}</td></tr></tbody></table></div>
    ${rows.length ? `<div class="sb-table"><table class="ro-table ro-picks"><thead><tr><th>신호일</th><th>종목</th><th>반등 확인</th><th>A 순수익</th><th>B 순수익</th><th>상태</th></tr></thead><tbody>
      ${rows.map((row) => `<tr><td>${esc(row.as_of)}</td><td><strong>${esc(row.name)}</strong><small>${esc(row.symbol)} · ${esc(row.market)}</small></td>
        <td>${row.confirmation ? (row.confirmation.confirmed ? '확인' : '아님') : '<span class="ro-na">대기</span>'}</td>
        <td>${armCell(row.arm_a)}</td><td>${armCell(row.arm_b)}</td><td class="sb-levels">${esc(SHADOW_STATUS[row.status] || row.status)}</td></tr>`).join('')}
    </tbody></table></div>` : '<p class="sb-empty">아직 관측할 신호가 없습니다.</p>'}
    <p class="sb-disclaimer">${esc(data.disclaimer)} 기준 문서: ${esc(data.preregistration)} (SHA-256 ${esc(String(data.preregistration_sha256).slice(0, 12))}…)</p>`;
}
