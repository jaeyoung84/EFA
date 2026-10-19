// 국내 종목 현재가 표시. 서버가 한투 체결가(설정 시) 또는 Yahoo 지연 시세를 돌려준다.
// 사용: 마크업에 liveSlot(symbol, market)을 넣고, 그린 뒤 startLiveQuotes(root)를 호출한다.
// root가 화면에서 사라지면 갱신을 멈춘다. 장중(한투 사용 시)에는 3초, 그 밖에는 30초마다 다시 읽는다.
import { authRequest, isAuthenticated } from '../auth.js';

const esc = (value) => String(value ?? '').replace(/[&<>'"]/g, (char) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
}[char]));
const MAX_TICKERS = 40;
export const TIME_FORMAT = { timeZone: 'Asia/Seoul', hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' };

export function liveTicker(symbol, market) {
  const code = String(symbol || '').trim().toUpperCase();
  if (!/^[0-9A-Z]{6}$/.test(code)) return '';
  return `${code}.${String(market || '').toUpperCase() === 'KOSDAQ' ? 'KQ' : 'KS'}`;
}

export function liveSlot(symbol, market) {
  const ticker = liveTicker(symbol, market);
  return ticker ? `<span class="live-quote is-loading" data-live-ticker="${esc(ticker)}">현재가 확인 중</span>` : '';
}

function timeOf(iso) {
  if (!iso) return '';
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleTimeString('ko-KR', TIME_FORMAT);
}

export function liveQuoteHtml(quote) {
  if (!quote || quote.status !== 'ok') {
    return `<span class="live-label">현재가</span> <span class="efa-muted" title="${esc(quote?.reason || '')}">조회 실패</span>`;
  }
  const pct = Number(quote.change_pct);
  const cls = pct > 0 ? 'is-up' : pct < 0 ? 'is-down' : '';
  const pctText = Number.isFinite(pct) ? `${pct > 0 ? '+' : ''}${pct.toFixed(2)}%` : '';
  const source = `${quote.source_label || '시세'}${quote.source === 'kis_realtime' ? ` ${timeOf(quote.quoted_at)}` : ''}`;
  const fallback = quote.fallback_reason ? ` title="${esc(quote.fallback_reason)}"` : '';
  return `<span class="live-label">현재가</span> <b>${Number(quote.price).toLocaleString('ko-KR')}</b> <i class="${cls}">${pctText}</i> <small${fallback}>${esc(source)}</small>`;
}

export async function fetchLiveQuotes(tickers) {
  return authRequest('/api/market/live-quotes', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ tickers: tickers.slice(0, MAX_TICKERS) }) });
}

export function startLiveQuotes(root) {
  root._stopLiveQuotes?.();
  let timer = null;
  let stopped = false;
  const stop = () => { stopped = true; clearTimeout(timer); };
  root._stopLiveQuotes = stop;
  const tick = async () => {
    if (stopped || !root.isConnected) return stop();
    const slots = [...root.querySelectorAll('[data-live-ticker]')];
    const tickers = [...new Set(slots.map((slot) => slot.dataset.liveTicker))];
    if (!tickers.length || !isAuthenticated()) {
      slots.forEach((slot) => { slot.classList.remove('is-loading'); slot.textContent = ''; });
      return stop();
    }
    let wait = 30;
    if (document.hidden) wait = 5;
    else {
      try {
        const data = await fetchLiveQuotes(tickers);
        const byTicker = Object.fromEntries((data.items || []).map((item) => [item.ticker, item]));
        slots.forEach((slot) => { slot.classList.remove('is-loading'); slot.innerHTML = liveQuoteHtml(byTicker[slot.dataset.liveTicker]); });
        // EINT-058: 같은 시세로 '현재가 기준' 표시(entryDrift.js)를 갱신하도록 알린다.
        root.dispatchEvent(new CustomEvent('livequotes', { detail: byTicker }));
        wait = Number(data.refresh_seconds) || 30;
      } catch {
        slots.forEach((slot) => { slot.classList.remove('is-loading'); slot.innerHTML = liveQuoteHtml(null); });
      }
    }
    if (!stopped) timer = setTimeout(tick, wait * 1000);
  };
  tick();
  return stop;
}
