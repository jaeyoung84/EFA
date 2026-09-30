// Human KQS recording helpers only. No orders, quotes, schedules or leverage DCA.
export function kstTradeAt(value) {
  if (!value) return null;
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?$/.test(value)) throw new Error('거래 시각 형식이 잘못됐습니다.');
  const instant = new Date(value + '+09:00');
  if (Number.isNaN(instant.getTime())) throw new Error('거래 시각을 확인하세요.');
  return instant.toISOString();
}
export function kstRecordTime(value) {
  if (!value) return '-';
  const instant = new Date(/[Zz]|[+-]\d{2}:\d{2}$/.test(value) ? value : value + 'Z');
  return Number.isNaN(instant.getTime()) ? '-' : instant.toLocaleString('ko-KR', { timeZone: 'Asia/Seoul' });
}
function newId() {
  const bytes = new Uint8Array(16);
  globalThis.crypto.getRandomValues(bytes); // also available when HTTP lacks randomUUID
  return 'kqs-' + [...bytes].map((n) => n.toString(16).padStart(2, '0')).join('');
}
export function manualRequest(form, body, idFactory = newId) {
  const fingerprint = JSON.stringify(body);
  if (!form._kqsManualRequest || form._kqsManualRequest.fingerprint !== fingerprint) {
    form._kqsManualRequest = { fingerprint, request_id: idFactory() };
  }
  return { ...body, request_id: form._kqsManualRequest.request_id };
}
export function resetManualRequest(form) { delete form._kqsManualRequest; }
