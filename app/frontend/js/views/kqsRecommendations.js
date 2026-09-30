const esc = (value) => String(value ?? '-').replace(/[&<>'"]/g, (char) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
}[char]));

const when = (value) => {
  if (!value) return '-';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? esc(value) : esc(date.toLocaleString('ko-KR'));
};

const KQS_DECISION_LABEL = { hold: '관망', buy: '모의 매수', exclude: '제외', sell: '모의 매도', review_again: '재검토' };

function decisionForm(signal) {
  const id = esc(signal.signal_id);
  return `<form data-kqs-decision-form data-kqs-signal-id="${id}" style="margin-top:10px; padding-top:10px; border-top:1px solid #253247;">
    <div style="display:flex; gap:6px; flex-wrap:wrap;">
      ${Object.entries(KQS_DECISION_LABEL).map(([value, label]) => `
        <label style="font-size:.7rem; color:var(--text); display:inline-flex; align-items:center; gap:4px; padding:4px 8px; border:1px solid #334155; border-radius:6px; cursor:pointer;">
          <input type="radio" name="decision-${id}" value="${value}" ${value === 'hold' ? 'checked' : ''} data-kqs-decision-radio style="margin:0;"> ${label}
        </label>`).join('')}
    </div>
    <div data-kqs-trade-fields style="display:none; gap:8px; margin-top:8px; flex-wrap:wrap;">
      <input type="number" min="0.000001" step="any" placeholder="수량" data-kqs-quantity class="param-input" style="width:90px;">
      <input type="number" min="0.01" step="any" placeholder="체결가" data-kqs-fill-price class="param-input" style="width:100px;">
      <input type="number" min="0" step="any" placeholder="수수료(원)" data-kqs-fee class="param-input" style="width:100px;">
      <input type="number" min="0" step="any" placeholder="세금(원)" data-kqs-tax class="param-input" style="width:100px;">
      <label style="font-size:.7rem;">거래 시각(KST, 생략 시 기록 시각)<input type="datetime-local" data-kqs-trade-at class="param-input"></label>
    </div>
    <input type="text" placeholder="근거 태그(쉼표로 구분, 예: 추세추종, 시총상위)" data-kqs-tags class="param-input" style="width:100%; margin-top:8px;">
    <textarea placeholder="판단 메모" data-kqs-memo class="param-input" style="width:100%; margin-top:8px; min-height:44px; resize:vertical;"></textarea>
    <button type="submit" class="run-btn" style="margin-top:8px; padding:7px 12px; font-size:.72rem;">판단 기록하기</button>
    <button type="button" data-kqs-new-record style="margin-left:6px; font-size:.7rem;">새 기록 시작</button>
    <span data-kqs-decision-status style="margin-left:8px; font-size:.7rem;"></span>
  </form>`;
}

function signalCard(signal) {
  const entry = signal.action === 'entry';
  const reasons = (signal.reasons || []).map((reason) => `
    <li style="margin-top:6px;"><strong>${esc(reason.label)}</strong>
      <span style="color:#94a3b8;">(${esc(reason.code)})</span><br>
      <span style="color:#cbd5e1;">${esc(reason.evidence)}</span></li>`).join('');
  return `<article style="padding:13px; background:#0f172a; border:1px solid #334155; border-radius:9px;">
    <header style="display:flex; justify-content:space-between; gap:10px; flex-wrap:wrap;">
      <div><strong style="color:var(--text);">${esc(signal.name)}</strong>
        <span style="color:#94a3b8; font-size:.72rem;">${esc(signal.symbol)} · ${esc(signal.market)}</span></div>
      <span style="color:${entry ? '#22c55e' : '#f59e0b'}; font-size:.75rem; font-weight:700;">
        ${entry ? '진입 관찰' : '청산 관찰'}${signal.selection_rank ? ` · ${esc(signal.selection_rank)}순위` : ''}</span>
    </header>
    <p style="margin:8px 0 0; color:#94a3b8; font-size:.72rem;">${esc(signal.signal_type)} · ${esc(signal.execution_session)} · ${esc(signal.selection_outcome)}</p>
    <ol style="margin:9px 0 0; padding-left:20px; font-size:.76rem; line-height:1.5;">${reasons}</ol>
    ${signal.exit_reference ? `<p style="color:#94a3b8; font-size:.7rem;">청산 참고: ${esc(signal.exit_reference)}</p>` : ''}
    ${signal.selection_note ? `<p style="color:#94a3b8; font-size:.7rem;">${esc(signal.selection_note)}</p>` : ''}
    ${decisionForm(signal)}
  </article>`;
}

export function kqsRecommendationsHtml(data) {
  if (!data?.read_only) return `<div><h2 style="margin:0 0 6px; color:var(--text); font-size:1rem;">KQS 추천 근거</h2>
    <p style="margin:0; color:#ef4444; font-size:.78rem;">검증된 추천을 사용할 수 없습니다. ${esc(data?.detail || '')}</p>
    <p style="margin:7px 0 0; color:#94a3b8; font-size:.7rem;">계약·무결성·만료 검증을 통과한 데이터만 표시합니다.</p></div>`;
  const engine = data.engine || {};
  const calendar = data.publication?.calendar || {};
  const cards = (data.signals || []).map(signalCard).join('');
  return `<header style="display:flex; justify-content:space-between; gap:12px; align-items:start; flex-wrap:wrap;">
    <div><h2 style="margin:0 0 5px; color:var(--text); font-size:1rem;">KQS 추천 근거</h2>
      <p style="margin:0; color:#94a3b8; font-size:.72rem;">읽기 전용 · 자동 모의체결 및 실거래 주문 없음</p></div>
    <button type="button" id="stg-kqs-recommendations-refresh" style="padding:7px 10px; color:var(--text); border:1px solid #334155; border-radius:7px; font-size:.72rem;">새로고침</button>
  </header>
  <div style="display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:8px; margin-top:12px; font-size:.72rem;">
    <div class="metric-box"><small>데이터 기준일</small><strong>${esc(engine.data_as_of)}</strong></div>
    <div class="metric-box"><small>생성 시각</small><strong>${when(data.generated_at)}</strong></div>
    <div class="metric-box"><small>만료 시각</small><strong>${when(data.expires_at)}</strong></div>
    <div class="metric-box"><small>선정 진입 (수익 예측 순위 아님)</small><strong>${esc(data.selected_entry_count ?? 0)}개</strong></div>
  </div>
  <div style="display:grid; gap:9px; margin-top:12px;">${cards || '<p style="margin:0; color:#94a3b8; font-size:.78rem;">현재 유효한 추천 신호가 없습니다.</p>'}</div>
  <details style="margin-top:10px; color:#94a3b8; font-size:.7rem;"><summary>엔진 추적 정보</summary>
    <p>엔진 ${esc(engine.engine_id)} · 버전 ${esc(engine.package_version)} · 실행 ${esc(engine.run_id)}</p>
    <p>게시 ${esc(data.snapshot_id)} · 원본 commit ${esc(engine.git_sha)} · 코드 ${esc(engine.code_sha256)} · 데이터셋 ${esc(engine.dataset_hash)}</p>
    <p>KRX 세션 ${esc(calendar.calendar_id)} · 공개 기대 ${esc(calendar.data_available_time_kst)} KST · 캘린더 ${esc(calendar.calendar_sha256)}</p></details>`;
}
