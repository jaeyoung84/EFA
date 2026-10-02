// 관심종목: 종목명으로 검색해 담고, 현재가·등락(한투 체결가, 없으면 지연 시세)을 본다. 본인 계정에만 저장된다.
// 알림·주문은 없다. 종목을 누르면 기업 재무 화면으로 이어진다.
import { authRequest, isAuthenticated, openAuthModal } from '../auth.js';
import { PENDING_STOCK_KEY } from '../shell.js';
import { attachStockSearch } from '../utils/stockSearch.js';
import { TIME_FORMAT, fetchLiveQuotes } from '../utils/liveQuotes.js';

const esc = (value) => String(value ?? '').replace(/[&<>'"]/g, (char) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
}[char]));
const num = (value) => value !== null && value !== undefined && Number.isFinite(Number(value));

export function watchlistHtml(items, quotes = {}, fetchedAt = null) {
  if (!items.length) {
    return '<div class="dash-empty"><p>관심종목이 없습니다. 위 검색창에서 종목명을 입력해 추가하세요.</p></div>';
  }
  const stamp = fetchedAt ? new Date(fetchedAt).toLocaleTimeString('ko-KR', TIME_FORMAT) : '-';
  const sources = [...new Set(Object.values(quotes).filter((quote) => quote?.status === 'ok').map((quote) => quote.source_label || '지연 시세'))];
  return `<div class="efa-table-wrap"><table class="efa-table">
    <thead><tr><th>종목</th><th>시장</th><th class="is-num">현재가</th><th class="is-num">등락률</th><th>메모</th><th>추가일</th><th></th></tr></thead>
    <tbody>${items.map((item) => {
      const quote = quotes[item.ticker];
      const ok = quote && quote.status === 'ok';
      const change = ok ? Number(quote.change_pct) : null;
      const cls = !num(change) || change === 0 ? '' : change > 0 ? 'is-up' : 'is-down';
      return `<tr>
        <td><button type="button" class="efa-link" data-watch-open="${esc(item.ticker)}" data-name="${esc(item.name)}"><strong>${esc(item.name)}</strong></button><small>${esc(item.code)}</small></td>
        <td>${esc(item.market)}</td>
        <td class="is-num ${cls}">${ok ? Number(quote.value ?? quote.price).toLocaleString('ko-KR') : `<span class="efa-muted">${quote ? '조회 실패' : '…'}</span>`}</td>
        <td class="is-num ${cls}">${num(change) ? `${change > 0 ? '+' : ''}${change.toFixed(2)}%` : '-'}</td>
        <td class="efa-muted">${esc(item.memo)}</td>
        <td class="efa-muted">${esc((item.added_at || '').slice(0, 10))}</td>
        <td><button type="button" class="efa-icon-btn is-small" data-watch-remove="${esc(item.ticker)}" aria-label="${esc(item.name)} 삭제"><i class="fa-solid fa-trash-can"></i></button></td>
      </tr>`;
    }).join('')}</tbody></table></div>
    <p class="dash-note">현재가 출처: ${esc(sources.join(', ') || '없음')}${sources.some((label) => label.includes('Yahoo')) ? '(약 15분 지연)' : ''} · 조회 ${esc(stamp)} · 최대 50종목.</p>`;
}

export function watchlistView(container) {
  container.innerHTML = `<div class="efa-page">
    <header class="efa-page-head"><div><h1><i class="fa-solid fa-star"></i> 관심종목</h1>
      <p>지켜볼 국내 종목을 담아 두고 현재가와 등락을 한눈에 봅니다. 종목을 누르면 기업 재무로 이동합니다.</p></div></header>
    <section class="dash-card" data-watch-body></section></div>`;
  const body = container.querySelector('[data-watch-body]');
  if (!isAuthenticated()) {
    body.innerHTML = '<div class="dash-empty"><p>관심종목은 로그인한 회원 본인 계정에 저장됩니다.</p><button type="button" class="dash-btn is-primary" data-login>로그인</button></div>';
    body.querySelector('[data-login]').addEventListener('click', openAuthModal);
    return;
  }
  body.innerHTML = `<form class="efa-form-row" data-watch-form>
      <input type="text" data-watch-search placeholder="종목명 검색 후 선택 (예: 삼성전자)" aria-label="추가할 종목명">
      <input type="text" data-watch-memo maxlength="200" placeholder="메모(선택)" aria-label="메모">
      <button type="submit" class="dash-btn is-primary">추가</button>
    </form>
    <p class="efa-error" role="status" data-watch-status></p>
    <div data-watch-list><p class="efa-muted">불러오는 중…</p></div>`;
  const search = body.querySelector('[data-watch-search]');
  const status = body.querySelector('[data-watch-status]');
  const list = body.querySelector('[data-watch-list]');
  let selected = null;
  attachStockSearch(search, { onSelect: (item) => { selected = item; } });
  search.addEventListener('input', () => { selected = null; });

  let items = [];
  let quotes = {};
  let fetchedAt = null;
  let timer = null;

  function render() {
    list.innerHTML = watchlistHtml(items, quotes, fetchedAt);
    list.querySelectorAll('[data-watch-remove]').forEach((button) => button.addEventListener('click', async () => {
      try { await authRequest(`/api/watchlist/${encodeURIComponent(button.dataset.watchRemove)}`, { method: 'DELETE' }); load(); }
      catch (error) { status.textContent = error.message; }
    }));
    list.querySelectorAll('[data-watch-open]').forEach((button) => button.addEventListener('click', () => {
      try { sessionStorage.setItem(PENDING_STOCK_KEY, JSON.stringify({ ticker: button.dataset.watchOpen, name: button.dataset.name })); } catch { /* noop */ }
      window.navigate('company-financial');
    }));
  }

  // 장중(한투 사용 시) 3초, 그 밖에는 30초마다 현재가만 다시 읽는다. 화면을 떠나면 멈춘다.
  async function refreshQuotes() {
    clearTimeout(timer);
    if (!list.isConnected || !items.length) return;
    let wait = 30;
    if (!document.hidden) {
      try {
        const tickers = items.map((item) => item.ticker);
        const chunks = [];
        for (let i = 0; i < tickers.length; i += 40) chunks.push(tickers.slice(i, i + 40));
        const results = await Promise.all(chunks.map(fetchLiveQuotes));
        quotes = Object.fromEntries(results.flatMap((data) => data.items || []).map((quote) => [quote.ticker, quote]));
        fetchedAt = results[0]?.fetched_at || null;
        wait = Number(results[0]?.refresh_seconds) || 30;
      } catch { /* 이전 시세를 그대로 두고 다음 주기에 다시 시도한다 */ }
      if (!list.isConnected) return;
      render();
    } else wait = 5;
    timer = setTimeout(refreshQuotes, wait * 1000);
  }

  async function load() {
    try {
      ({ items } = await authRequest('/api/watchlist'));
      render();
      refreshQuotes();
    } catch (error) { list.innerHTML = `<p class="efa-error">${esc(error.message)}</p>`; }
  }

  body.querySelector('[data-watch-form]').addEventListener('submit', async (event) => {
    event.preventDefault();
    if (!selected) { status.textContent = '목록에서 종목을 골라 주세요.'; return; }
    status.textContent = '';
    try {
      await authRequest('/api/watchlist', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ticker: selected.ticker, memo: body.querySelector('[data-watch-memo]').value }) });
      search.value = ''; body.querySelector('[data-watch-memo]').value = ''; selected = null;
      load();
    } catch (error) { status.textContent = error.message; }
  });
  load();
}
