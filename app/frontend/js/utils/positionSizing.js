// EINT-027: 추천 카드의 권장 매수 수량(변동성 기반 포지션 사이징). 참고 계산이며 주문하지 않는다.
const esc = (value) => String(value ?? '').replace(/[&<>'"]/g, (char) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
}[char]));
const won = (value) => `${Math.round(Number(value)).toLocaleString('ko-KR')}원`;
export const RISK_PCT_KEY = 'efa_risk_pct';
export const RISK_PCT_DEFAULT = 1;

export function readRiskPct() {
  try {
    const value = Number(localStorage.getItem(RISK_PCT_KEY));
    return Number.isFinite(value) && value >= 0.25 && value <= 3 ? value : RISK_PCT_DEFAULT;
  } catch { return RISK_PCT_DEFAULT; }
}

export function saveRiskPct(value) {
  try { localStorage.setItem(RISK_PCT_KEY, String(value)); } catch { /* 저장 불가: 이번 화면에서만 */ }
}

export function sizingHtml(entry, sizing) {
  if (!sizing) return '<p class="pos-size is-muted">권장 수량 계산 중…</p>';
  if (!sizing.available) return `<p class="pos-size is-muted">권장 수량: ${esc(sizing.reason || '계산할 수 없습니다.')}</p>`;
  if (!entry) return '';
  if (!entry.available) return `<p class="pos-size is-muted">권장 수량: ${esc(entry.reason || '계산할 수 없습니다.')}</p>`;
  const flags = [];
  if (entry.capped_by === 'max_position') flags.push(`종목당 최대 비중 ${esc(sizing.max_position_pct)}%로 줄임`);
  if (entry.cash_short) flags.push(`현금으로는 ${esc(entry.cash_quantity)}주까지`);
  if (sizing.equity_partial) flags.push('시세 확인된 자산 기준');
  const riskPctOfEquity = sizing.equity_krw ? (entry.risk_krw / sizing.equity_krw * 100).toFixed(2) : '-';
  return `<div class="pos-size" title="${esc(entry.basis_label)} · ATR ${esc(Math.round(entry.atr).toLocaleString('ko-KR'))}원">
    <p><b>권장 매수</b> <strong>${esc(Number(entry.quantity).toLocaleString('ko-KR'))}주</strong>
      ≈ ${esc(won(entry.amount_krw))} <small>(자산의 ${esc(entry.weight_pct ?? '-')}%, ${esc(entry.price_basis)} ${esc(won(entry.price))})</small></p>
    <p class="pos-size-risk">${entry.basis === 'v4_stop' ? '손절 시' : '2×ATR 하락 시'} 손실 ≈ ${esc(won(entry.risk_krw))} <small>(자산의 ${esc(riskPctOfEquity)}% · 한도 ${esc(sizing.risk_pct)}%)</small></p>
    <p class="pos-size-basis">${esc(entry.basis_label)}${flags.length ? ` · ${flags.join(' · ')}` : ''}</p>
  </div>`;
}
