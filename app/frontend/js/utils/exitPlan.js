// EINT-026: 추천 카드의 현행 청산 규칙을 가격·수치로 보여 준다(참고 표시, 자동 매도 없음).
// Codex 수정: CODX-EXIT-001 (2026-10-12) — 성과 검증 완료·체결 확정처럼 읽히지 않도록 표현한다.
// 목표가·손익비는 어느 전략에도 규칙이 없어 표시하지 않는다.
const esc = (value) => String(value ?? '').replace(/[&<>'"]/g, (char) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
}[char]));
const won = (value) => `${Math.round(Number(value)).toLocaleString('ko-KR')}원`;
const signedPct = (value) => `${Number(value) > 0 ? '+' : ''}${Number(value).toFixed(1)}%`;

function ruleHtml(rule) {
  if (rule.price !== undefined && rule.price !== null) {
    const tone = Number(rule.pct) < 0 ? 'is-down' : 'is-up';
    return `<li><span>${esc(rule.label)}</span><b>≈ ${esc(won(rule.price))}</b> <i class="${tone}">${esc(signedPct(rule.pct))}</i></li>`;
  }
  if (rule.kind === 'indicator') {
    return `<li><span>${esc(rule.label)}</span><b>현재 ${esc(Number(rule.current).toFixed(1))}</b></li>`;
  }
  return `<li><span>${esc(rule.label)}</span></li>`;
}

export function exitPlanHtml(plan) {
  if (!plan || !Array.isArray(plan.rules) || !plan.rules.length) return '';
  return `<div class="exit-plan" title="${esc(plan.basis || '')}">
    <p class="exit-plan-head"><b>청산 참고 기준</b><small>${esc(plan.note || '')}</small></p>
    <ul>${plan.rules.map(ruleHtml).join('')}</ul>
    ${plan.basis ? `<p class="exit-plan-basis">${esc(plan.basis)}</p>` : ''}
  </div>`;
}
