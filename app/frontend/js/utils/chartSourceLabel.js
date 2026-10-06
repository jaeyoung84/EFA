// 일봉 차트 출처 표기(2026-10-06): 고정 문구 '15분 지연' 대신 마지막 봉 날짜를 보여 주고, 갱신이 멈춘 저장본이면 경고한다.
export function chartSourceLabel(data) {
  if (data?.is_simulated) return { text: '시뮬레이션 데이터', stale: false };
  const last = String(data?.last_bar_date || '');
  const day = /^\d{4}-\d{2}-\d{2}$/.test(last) ? `${last.slice(5, 7)}/${last.slice(8, 10)}` : '';
  if (data?.stale) return { text: `마지막 봉 ${day || '미확인'} · 시세 갱신 지연`, stale: true };
  return { text: day ? `Yahoo Finance · ${day} 일봉 · 최대 15분 지연` : 'Yahoo Finance · 최대 15분 지연', stale: false };
}
