// 외부 추천 기록: 인베스팅닷컴 ProPicks·저평가 스크리너 등 외부 추천 명단을 매달 올리고 이후 성과를 SPY·KODEX 200과 비교한다.
// 명단은 본인 계정에만 저장되며, 기록 이후의 전향 관측만 한다(과거 백테스트 아님, 투자 권유 아님).
import { authRequest, isAuthenticated, openAuthModal } from '../auth.js';

const esc = (value) => String(value ?? '').replace(/[&<>'"]/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[char]));
const signed = (value) => (value === null || value === undefined ? '-' : `${value > 0 ? '+' : ''}${Number(value).toFixed(2)}%`);
const tone = (value) => (value > 0 ? 'is-up' : value < 0 ? 'is-down' : '');
export const SOURCE_PRESETS = ['InvestingPro ProPicks', 'InvestingPro 저평가(Fair Value)', 'InvestingPro AI 스크리너'];
const todayKst = () => new Date(Date.now() + 9 * 3600 * 1000).toISOString().slice(0, 10);

export function previewHtml(parsed) {
  if (!parsed) return '';
  const chips = parsed.recognized.map((item, index) => `<label class="xp-chip"><input type="checkbox" data-xp-pick="${index}" checked>
    <b>${esc(item.ticker)}</b><small>${esc(item.name)}${item.market === 'KR' ? ' · 국내' : ''}</small></label>`).join('');
  return `<div class="xp-preview">
    <p class="dash-note">${parsed.mode === 'column' ? '티커 열에서 읽었습니다.' : '글에서 티커로 보이는 단어를 골랐습니다. 아닌 것은 체크를 해제하세요.'}
      인식 ${parsed.recognized.length}개${parsed.truncated ? ' (최대 60개까지만)' : ''}</p>
    <div class="xp-chips">${chips || '<span class="rrg-muted">인식된 종목이 없습니다.</span>'}</div>
    ${parsed.unrecognized?.length ? `<p class="dash-note">인식하지 못함: ${parsed.unrecognized.map(esc).join(', ')}</p>` : ''}
  </div>`;
}

function positionsTable(positions, pending) {
  if (!positions.length) return `<p class="dash-note">${pending.length ? '시세를 받는 중입니다. 잠시 뒤 새로고침하세요.' : '아직 기록 다음 거래일이 오지 않았습니다.'}</p>`;
  return `<div class="efa-table-wrap"><table class="efa-table xp-table"><thead><tr><th>종목</th><th>진입</th><th class="is-num">수익률</th>
    <th class="is-num xp-opt">벤치마크</th><th class="is-num">초과</th><th class="is-num xp-opt">1개월</th><th class="is-num xp-opt">3개월</th></tr></thead><tbody>
    ${positions.map((p) => `<tr><td><b>${esc(p.ticker)}</b><small>${esc(p.name)}</small></td><td>${esc(p.entry_date)}</td>
      <td class="is-num ${tone(p.return_pct)}">${signed(p.return_pct)}</td><td class="is-num xp-opt">${signed(p.benchmark_return_pct)}</td>
      <td class="is-num ${tone(p.excess_pct)}">${signed(p.excess_pct)}</td><td class="is-num xp-opt">${signed(p.horizons?.['1m'])}</td>
      <td class="is-num xp-opt">${signed(p.horizons?.['3m'])}</td></tr>`).join('')}
  </tbody></table></div>${pending.length ? `<p class="dash-note">시세 대기: ${pending.map(esc).join(', ')}</p>` : ''}`;
}

export function listCardHtml(list) {
  const s = list.summary;
  return `<article class="dash-card xp-list" data-xp-list="${esc(list.id)}">
    <header class="dash-head"><h2>${esc(list.list_name)}</h2><span class="dash-sub">${esc(list.source)} · 기록 ${esc(list.as_of)} · ${list.count}종목</span>
      <button type="button" class="dash-link" data-xp-delete="${esc(list.id)}">삭제</button></header>
    ${s ? `<div class="xp-summary">
      <div><span>동일 비중 수익률</span><strong class="${tone(s.return_pct)}">${signed(s.return_pct)}</strong></div>
      <div><span>벤치마크</span><strong>${signed(s.benchmark_return_pct)}</strong></div>
      <div><span>초과 수익</span><strong class="${tone(s.excess_pct)}">${signed(s.excess_pct)}</strong></div>
      <div><span>벤치마크를 이긴 종목</span><strong>${s.beat_rate_pct}%</strong><small>${s.priced}종목 · ${s.sessions}거래일</small></div>
    </div>` : ''}
    <details><summary>종목별 성과</summary>${positionsTable(list.positions, list.pending)}</details>
  </article>`;
}

export function trackHtml(source, track) {
  if (!track.months) return '';
  return `<article class="dash-card xp-track">
    <header class="dash-head"><h2>${esc(source)}</h2><span class="dash-sub">매달 명단을 갈아탔을 때 · ${track.months}개 명단 · 교체당 0.1% 차감</span></header>
    <div class="xp-summary">
      <div><span>연결 수익률</span><strong class="${tone(track.return_pct)}">${signed(track.return_pct)}</strong></div>
      <div><span>벤치마크</span><strong>${signed(track.benchmark_return_pct)}</strong></div>
      <div><span>초과 수익</span><strong class="${tone(track.excess_pct)}">${signed(track.excess_pct)}</strong></div>
    </div>
    ${track.months < 12 ? `<p class="dash-note">명단 ${track.months}개로는 우연과 실력을 가르기 어렵습니다. 12개월 이상 쌓인 뒤 판단하세요.</p>` : ''}
  </article>`;
}

export function externalPicksView(container) {
  container.innerHTML = `<div class="efa-page xp-page">
    <header class="efa-page-head"><div><h1><i class="fa-solid fa-clipboard-list"></i> 외부 추천 기록</h1>
      <p>인베스팅닷컴 ProPicks·저평가 스크리너 같은 외부 추천 명단을 매달 올리면, 기록한 다음 거래일부터의 성과를 SPY(미국)·KODEX 200(국내)과 자동으로 비교합니다.
      명단은 본인 계정에만 저장됩니다.</p></div></header>
    <div data-xp-body><section class="dash-card"><p class="rrg-muted">불러오는 중…</p></section></div></div>`;
  const body = container.querySelector('[data-xp-body]');
  if (!isAuthenticated()) {
    body.innerHTML = '<section class="dash-card"><div class="dash-empty"><p>외부 추천 기록은 로그인한 회원 본인 계정에 저장됩니다.</p><button type="button" class="dash-btn is-primary" data-login>로그인</button></div></section>';
    body.querySelector('[data-login]').addEventListener('click', openAuthModal);
    return;
  }
  let parsed = null;
  body.innerHTML = `<section class="dash-card xp-input">
      <header class="dash-head"><h2>명단 올리기</h2><span class="dash-sub">CSV 파일 또는 화면 표를 복사해 붙여 넣기</span></header>
      <div class="xp-form">
        <label>출처<input data-xp-source list="xp-sources" value="${esc(SOURCE_PRESETS[0])}" maxlength="60"></label>
        <datalist id="xp-sources">${SOURCE_PRESETS.map((s) => `<option value="${esc(s)}">`).join('')}</datalist>
        <label>명단 이름<input data-xp-name placeholder="예: 10월 Tech Titans" maxlength="80"></label>
        <label>기록 날짜<input type="date" data-xp-date value="${todayKst()}" max="${todayKst()}"></label>
      </div>
      <textarea data-xp-text rows="6" placeholder="InvestingPro 화면의 표를 복사해 붙여 넣거나 아래에서 CSV 파일을 고르세요."></textarea>
      <div class="xp-actions"><label class="dash-btn"><i class="fa-solid fa-file-csv"></i> CSV 파일<input type="file" accept=".csv,.txt,.tsv" data-xp-file hidden></label>
        <button type="button" class="dash-btn" data-xp-parse>종목 인식</button>
        <button type="button" class="dash-btn is-primary" data-xp-save disabled>저장</button></div>
      <div data-xp-preview></div><p class="dash-note" data-xp-status aria-live="polite"></p>
    </section>
    <div data-xp-report></div>`;
  const $ = (sel) => body.querySelector(sel);
  const status = (text, error = false) => { $('[data-xp-status]').textContent = text; $('[data-xp-status]').classList.toggle('is-error', error); };
  $('[data-xp-file]').addEventListener('change', async (event) => {
    const file = event.target.files?.[0];
    if (file) { $('[data-xp-text]').value = await file.text(); status(`${file.name}을(를) 읽었습니다. "종목 인식"을 누르세요.`); }
  });
  $('[data-xp-parse]').addEventListener('click', async () => {
    const text = $('[data-xp-text]').value.trim();
    if (!text) { status('명단을 붙여 넣거나 CSV 파일을 고르세요.', true); return; }
    try {
      parsed = await authRequest('/api/external-picks/parse', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text }) });
      $('[data-xp-preview]').innerHTML = previewHtml(parsed);
      $('[data-xp-save]').disabled = !parsed.recognized.length;
      status('');
    } catch (error) { status(error.message, true); }
  });
  $('[data-xp-save]').addEventListener('click', async () => {
    const items = parsed.recognized.filter((_, index) => body.querySelector(`[data-xp-pick="${index}"]`)?.checked);
    if (!items.length) { status('저장할 종목을 하나 이상 선택하세요.', true); return; }
    $('[data-xp-save]').disabled = true;
    try {
      await authRequest('/api/external-picks', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({
        source: $('[data-xp-source]').value.trim(), list_name: $('[data-xp-name]').value.trim(), as_of: $('[data-xp-date]').value,
        items, raw_text: $('[data-xp-text]').value }) });
      status(`${items.length}종목을 저장했습니다. 시세를 받는 중이라 성과는 잠시 뒤 표시됩니다.`);
      $('[data-xp-text]').value = ''; $('[data-xp-preview]').innerHTML = ''; parsed = null;
      loadReport();
    } catch (error) { status(error.message, true); $('[data-xp-save]').disabled = false; }
  });
  const loadReport = async () => {
    const box = $('[data-xp-report]');
    try {
      const data = await authRequest('/api/external-picks');
      const tracks = Object.entries(data.tracks || {}).map(([source, track]) => trackHtml(source, track)).join('');
      box.innerHTML = `${tracks}${data.lists.map(listCardHtml).join('') || '<section class="dash-card"><p class="dash-note">아직 올린 명단이 없습니다.</p></section>'}
        <p class="dash-note">${esc(data.note)}${data.prices_updated_at ? ` · 시세 갱신 ${esc(new Date(data.prices_updated_at).toLocaleString('ko-KR', { timeZone: 'Asia/Seoul' }))}` : ''}</p>`;
      box.querySelectorAll('[data-xp-delete]').forEach((button) => button.addEventListener('click', async () => {
        if (!window.confirm('이 명단을 삭제할까요? 성과 기록도 함께 사라집니다.')) return;
        try { await authRequest(`/api/external-picks/${button.dataset.xpDelete}`, { method: 'DELETE' }); loadReport(); } catch (error) { status(error.message, true); }
      }));
    } catch (error) { box.innerHTML = `<section class="dash-card"><p class="dash-note is-error">${esc(error.message)}</p></section>`; }
  };
  loadReport();
}
