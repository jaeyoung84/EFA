// 전략 추천 (EINT-017): 3개 아이템의 오늘 추천 종목(아이템별 최대 5개)과 내 보유 종목 매도 신호를 한 화면에 보여 준다.
// 읽기 전용이다. 매수·매도 기록은 "기록·연구" 화면에서 직접 한다(자동 주문·자동 모의체결 없음).
import { authRequest, isAuthenticated, openAuthModal } from '../auth.js';

const esc = (value) => String(value ?? '-').replace(/[&<>'"]/g, (char) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
}[char]));
const kst = (value) => {
  if (!value) return '-';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? esc(value) : esc(date.toLocaleString('ko-KR', { timeZone: 'Asia/Seoul' }));
};
const price = (value) => (Number.isFinite(Number(value)) ? Number(value).toLocaleString('ko-KR', { maximumFractionDigits: 2 }) : '-');

const STATE_LABEL = {
  exit_signal: ['매도 신호', 'is-sell'], hold: ['보유 유지', 'is-hold'], no_item: ['아이템 없음', 'is-muted'],
  not_monitored: ['모니터 대상 아님', 'is-muted'], unavailable: ['판단 불가', 'is-muted'],
};
const ITEM_SHORT = { swing: 'Bollinger-RSI', long_term: 'RSI-OBV', pullback_v4: 'Pullback v4' };

function pickRow(pick) {
  return `<li class="sb-pick">
    <details>
      <summary><span class="sb-rank">${esc(pick.rank)}</span><span class="sb-name"><strong>${esc(pick.name)}</strong><small>${esc(pick.symbol)} · ${esc(pick.market)}</small></span><span class="sb-close">${price(pick.close)}</span></summary>
      <ul class="sb-reasons">${(pick.reasons || []).map((reason) => `<li>${esc(reason)}</li>`).join('')}</ul>
      ${pick.exit_reference ? `<p class="sb-note"><b>청산 기준</b> ${esc(pick.exit_reference)}</p>` : ''}
    </details>
  </li>`;
}

function itemCard(item) {
  const body = !item.published
    ? '<p class="sb-empty">이 아이템은 엔진 v2 게시 후부터 표시됩니다.</p>'
    : item.picks.length
      ? `<ol class="sb-picks">${item.picks.map(pickRow).join('')}</ol>`
      : '<p class="sb-empty">오늘은 이 아이템의 조건을 만족한 종목이 없습니다.</p>';
  return `<article class="sb-item" data-item="${esc(item.item)}">
    <header><span class="sb-label">${esc(item.label)}</span><h2>${esc(item.title)}</h2>
      <span class="sb-count">${item.published ? `${item.picks.length} / ${item.max_picks}` : '-'}</span></header>
    <p class="sb-summary">${esc(item.summary)}</p>
    ${body}
    <footer><span>정렬: ${esc(item.order)}</span><span>청산: ${esc(item.exit)}</span></footer>
  </article>`;
}

function sellRow(row) {
  const [label, tone] = STATE_LABEL[row.state] || [row.state, 'is-muted'];
  const levels = row.item === 'pullback_v4' && row.state !== 'no_item' && row.stop_price !== undefined
    ? `손절 ${price(row.stop_price)} · 트레일링 ${price(row.trailing_level)} · 보유 ${row.holding_days ?? '15일 초과'}/${row.holding_limit}일${row.market_regime_off ? ' · 시장 50일선 이탈' : ''}`
    : (row.detail || '');
  return `<tr class="${row.state === 'exit_signal' ? 'is-alert' : ''}">
    <td><strong>${esc(row.name || row.symbol)}</strong><small>${esc(row.symbol)}</small></td>
    <td>${esc(ITEM_SHORT[row.item] || '-')}</td>
    <td>${esc(row.bought_on)}<small>${price(row.fill_price)}원 · ${esc(row.remaining_quantity)}주</small></td>
    <td>${row.close !== undefined ? price(row.close) : '-'}</td>
    <td><span class="sb-badge ${tone}">${esc(label)}</span>${row.reason_label ? `<small>${esc(row.reason_label)}</small>` : ''}</td>
    <td class="sb-levels">${esc(levels)}</td>
  </tr>`;
}

export function boardHtml(data) {
  const meta = data.available
    ? `데이터 기준일 <b>${esc(data.as_of)}</b> · 만료 ${kst(data.expires_at)} · 엔진 ${esc(data.package_version)}`
    : `<span class="sb-error">검증된 추천을 사용할 수 없습니다. ${esc(data.detail || '')}</span>`;
  const sells = data.sell_signals || [];
  const alerts = sells.filter((row) => row.state === 'exit_signal').length;
  return `
    <section class="sb-head">
      <div><h1><i class="fa-solid fa-bullseye"></i> 전략 추천</h1><p>${meta}</p></div>
      <div class="sb-actions"><button type="button" class="run-btn" data-sb-refresh><i class="fa-solid fa-rotate"></i> 새로고침</button>
        <button type="button" data-sb-record><i class="fa-solid fa-pen-to-square"></i> 매수·매도 기록하기</button></div>
    </section>
    <section class="sb-grid">${(data.items || []).map(itemCard).join('')}</section>
    <section class="sb-sells">
      <header><h2>내 보유 종목 매도 신호</h2><span class="sb-count ${alerts ? 'is-alert' : ''}">${alerts}건</span></header>
      ${sells.length ? `<div class="sb-table"><table><thead><tr><th>종목</th><th>아이템</th><th>매수일·가격</th><th>기준 종가</th><th>신호</th><th>세부</th></tr></thead>
        <tbody>${sells.map(sellRow).join('')}</tbody></table></div>`
        : '<p class="sb-empty">기록된 보유 종목이 없습니다. 매수하면 "매수·매도 기록하기"에서 기록하세요.</p>'}
      <p class="sb-note">매도 신호는 그 종목을 추천한 아이템의 청산 기준입니다. Pullback v4의 손절·트레일링은 다음 날 시가 대신 기준일 종가로 미리 알려 줍니다.</p>
    </section>
    <p class="sb-disclaimer">${esc(data.disclaimer)}</p>`;
}

export function strategyBoardView(container) {
  if (!isAuthenticated()) {
    container.innerHTML = `<section class="sb-login"><h1>전략 추천을 보려면 로그인하세요</h1>
      <p>3개 전략의 오늘 추천 종목과 내 보유 종목의 매도 신호를 한 화면에서 확인합니다.</p>
      <button type="button" class="run-btn" data-sb-login>로그인하고 시작하기</button></section>`;
    container.querySelector('[data-sb-login]').addEventListener('click', openAuthModal);
    return;
  }
  const load = async () => {
    container.innerHTML = '<p class="sb-empty">전략 추천을 불러오는 중…</p>';
    try {
      container.innerHTML = boardHtml(await authRequest('/api/kqs/strategy-board'));
    } catch (error) {
      container.innerHTML = `<p class="sb-error">전략 추천을 불러오지 못했습니다. ${esc(error.message)}</p>`;
    }
    container.querySelector('[data-sb-refresh]')?.addEventListener('click', load);
    container.querySelector('[data-sb-record]')?.addEventListener('click', () => window.navigate('my-strategies'));
  };
  load();
}
