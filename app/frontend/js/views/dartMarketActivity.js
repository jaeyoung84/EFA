import { api } from '../api.js';

const esc = (value) => String(value ?? '').replace(/[&<>'"]/g, (char) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
}[char]));

const LABELS = {
  treasury_decision: '자사주 취득 결정',
  treasury_trust: '자사주 신탁계약',
  treasury_result: '자사주 취득 결과',
  insider_increase: '임원·주요주주 증가',
  insider_decrease: '임원·주요주주 감소',
  insider_change: '임원·주요주주 변동',
  major_increase: '5% 이상 보유 증가',
  major_decrease: '5% 이상 보유 감소',
  major_change: '5% 이상 보유 변동',
};

const GROUPS = {
  all: () => true,
  treasury: (kind) => kind.startsWith('treasury'),
  insider_increase: (kind) => kind === 'insider_increase',
  insider_decrease: (kind) => kind === 'insider_decrease',
  major: (kind) => kind.startsWith('major'),
};

// 시장 구분·날짜별 구분(2026-10-06, 사용자 승인).
const MARKET_LABELS = { KOSPI: '코스피', KOSDAQ: '코스닥', KONEX: '코넥스', 기타: '기타' };
const MARKET_FILTERS = [['all', '전체 시장'], ['KOSPI', '코스피'], ['KOSDAQ', '코스닥'], ['기타', '코넥스·기타']];
const marketOf = (item) => (item.market === 'KONEX' ? '기타' : item.market || '기타');
const WEEKDAYS = ['일', '월', '화', '수', '목', '금', '토'];

export function formatDisclosureDate(raw) {
  const text = String(raw || '').replace(/-/g, '');
  if (!/^\d{8}$/.test(text)) return '접수일 미확인';
  const [y, m, d] = [text.slice(0, 4), text.slice(4, 6), text.slice(6, 8)];
  const weekday = WEEKDAYS[new Date(Date.UTC(+y, +m - 1, +d)).getUTCDay()];
  return `${y}.${m}.${d} (${weekday})`;
}

/** 최신 접수일부터 날짜별로 묶는다(같은 날 안에서는 접수번호 역순). */
export function groupByDate(items) {
  const sorted = [...items].sort((a, b) => String(b.rcept_date || '').localeCompare(String(a.rcept_date || ''))
    || String(b.rcept_no || '').localeCompare(String(a.rcept_no || '')));
  const groups = [];
  for (const item of sorted) {
    const key = String(item.rcept_date || '');
    if (!groups.length || groups[groups.length - 1].key !== key) groups.push({ key, label: formatDisclosureDate(key), items: [] });
    groups[groups.length - 1].items.push(item);
  }
  return groups;
}

const number = (value) => Number.isFinite(Number(value)) ? Number(value).toLocaleString() : '-';
const signed = (value, suffix = '') => Number.isFinite(Number(value))
  ? `${Number(value) > 0 ? '+' : ''}${Number(value).toLocaleString()}${suffix}` : '-';

function row(item) {
  const kind = item.kind || '';
  const reporter = item.reporter || '-';
  const position = item.position ? ` · ${esc(item.position)}` : '';
  const reason = item.report_reason ? `<small>${esc(item.report_reason)}</small>` : '';
  return `<tr data-kind="${esc(kind)}">
    <td><span class="dart-activity-kind ${esc(kind)}">${esc(LABELS[kind] || '지분 변동')}</span></td>
    <td><strong>${esc(item.corp_name)}</strong><small><span class="dart-activity-market is-${esc((item.market || '기타').toLowerCase())}">${esc(MARKET_LABELS[item.market] || '기타')}</span> ${esc(item.stock_code || '종목코드 미표기')}</small></td>
    <td><strong>${esc(reporter)}</strong><small>${esc(item.relationship || '')}${position}</small>${reason}</td>
    <td class="dart-activity-number">${number(item.holding_count)}<small>증감 ${signed(item.change_count)}</small></td>
    <td class="dart-activity-number">${signed(item.holding_rate, '%')}<small>증감 ${signed(item.change_rate, '%')}</small></td>
    <td>${esc(item.rcept_date || '-')}<small>${esc(item.report_name || '')}</small></td>
    <td>${item.dart_url ? `<a class="dart-activity-link" href="${esc(item.dart_url)}" target="_blank" rel="noopener noreferrer">원문 <i class="fa-solid fa-arrow-up-right-from-square"></i></a>` : '-'}</td>
  </tr>`;
}

export function dartMarketActivityView(container) {
// Codex 수정: DART-001 (2026-10-06), 최근 7일 공시 안내와 기존 표시본 보존.
  container.innerHTML = `
    <section class="dart-activity-page">
      <header class="dart-activity-head">
        <div>
          <p class="dart-activity-kicker">READ-ONLY · OPEN DART</p>
          <h1><i class="fa-solid fa-building-shield"></i> 자사주·내부자 지분 변동</h1>
          <p>자사주 취득과 법정 신고 대상 임원·주요주주·5% 이상 보유자의 공시를 한 화면에 모읍니다.</p>
        </div>
        <div class="dart-activity-stamp" id="dart-activity-stamp">수집 기록 조회 중</div>
      </header>
      <section class="dart-activity-guide">
        <i class="fa-solid fa-circle-info" aria-hidden="true"></i>
        <p><strong>최근 7일 공시 · 평일 20:00 KST 수집</strong> · 보유수량 증가는 장내 매수를 뜻한다고 단정할 수 없습니다. 스톡옵션 행사·증여·전환 등은 DART 원문을 확인하세요.</p>
      </section>
      <div class="dart-activity-summary" id="dart-activity-summary"></div>
      <div class="dart-activity-toolbar" aria-label="공시 유형 필터">
        <div class="dart-activity-filters">
          <button class="active" data-dart-filter="all">전체</button>
          <button data-dart-filter="treasury">자사주</button>
          <button data-dart-filter="insider_increase">내부자 증가</button>
          <button data-dart-filter="insider_decrease">내부자 감소</button>
          <button data-dart-filter="major">5% 이상</button>
        </div>
        <div class="dart-activity-filters dart-activity-markets" aria-label="시장 필터"></div>
        <button class="dart-activity-refresh" type="button" id="dart-activity-refresh"><i class="fa-solid fa-rotate-right"></i> 저장된 결과 새로고침</button>
      </div>
      <div class="dart-activity-table-wrap">
        <table class="dart-activity-table">
          <thead><tr><th>유형</th><th>회사</th><th>보고자</th><th>보유수량</th><th>보유비율</th><th>접수일 · 공시</th><th>원문</th></tr></thead>
          <tbody id="dart-activity-list"><tr><td colspan="7" class="dart-activity-empty"><i class="fa-solid fa-spinner fa-spin"></i> 저장된 수집 기록을 불러오는 중입니다.</td></tr></tbody>
        </table>
      </div>
      <p class="dart-activity-note" id="dart-activity-note"></p>
    </section>`;

  const list = container.querySelector('#dart-activity-list');
  const summary = container.querySelector('#dart-activity-summary');
  const stamp = container.querySelector('#dart-activity-stamp');
  const note = container.querySelector('#dart-activity-note');
  const refresh = container.querySelector('#dart-activity-refresh');
  const markets = container.querySelector('.dart-activity-markets');
  let items = [];
  let selectedFilter = 'all';
  let selectedMarket = 'all';

  const renderMarkets = () => {
    const count = (key) => items.filter((item) => key === 'all' || marketOf(item) === key).length;
    markets.innerHTML = MARKET_FILTERS.map(([key, label]) => `<button class="${key === selectedMarket ? 'active' : ''}" data-dart-market="${esc(key)}">${label} <b>${count(key).toLocaleString()}</b></button>`).join('');
  };

  const render = () => {
    const visible = items.filter((item) => (GROUPS[selectedFilter] || GROUPS.all)(item.kind || '')
      && (selectedMarket === 'all' || marketOf(item) === selectedMarket));
    list.innerHTML = visible.length ? groupByDate(visible).map((group) => `<tr class="dart-activity-date"><td colspan="7"><span><i class="fa-regular fa-calendar"></i> ${esc(group.label)} · ${group.items.length.toLocaleString()}건</span></td></tr>${group.items.map(row).join('')}`).join('') :
      '<tr><td colspan="7" class="dart-activity-empty">조건에 맞는 공시가 없습니다.</td></tr>';
  };

  const renderSummary = (counts) => {
    const cards = [
      ['treasury', '자사주 관련', 'fa-building-columns'],
      ['insider_increase', '내부자 증가', 'fa-arrow-trend-up'],
      ['insider_decrease', '내부자 감소', 'fa-arrow-trend-down'],
      ['major', '5% 이상 변동', 'fa-users'],
    ];
    summary.innerHTML = cards.map(([key, label, icon]) => `<article>
      <i class="fa-solid ${icon}" aria-hidden="true"></i><span>${label}</span><strong>${Number(counts?.[key] || 0).toLocaleString()}건</strong>
    </article>`).join('');
  };

  async function load() {
    refresh.disabled = true;
    try {
      const data = await api.dartMarketActivity();
      items = data.items || [];
      renderSummary(data.counts || {});
      renderMarkets();
      const period = data.window_start && data.window_end
        ? `${formatDisclosureDate(data.window_start)} ~ ${formatDisclosureDate(data.window_end)}` : '최근 7일';
      stamp.textContent = data.snapshot_date
        ? `${period} · 최종 수집 ${data.snapshot_date}`
        : `${period} · 수집 기록 없음`;
      note.textContent = data.note || 'OpenDART 공시정보 · 읽기 전용';
      if (data.status === 'pending') {
        list.innerHTML = '<tr><td colspan="7" class="dart-activity-empty"><i class="fa-regular fa-clock"></i> 최근 7일 수집된 공시가 없습니다.</td></tr>';
      } else {
        render();
      }
    } catch (error) {
      // Codex 수정: DART-001 (2026-10-06), 조회 실패 시 이미 표시한 수집본 유지.
      if (items.length) {
        note.textContent = `새로고침 실패 · 이전에 표시한 공시를 유지합니다: ${error.message}`;
      } else {
        list.innerHTML = `<tr><td colspan="7" class="dart-activity-empty is-error">수집 기록을 불러오지 못했습니다: ${esc(error.message)}</td></tr>`;
        note.textContent = 'OpenDART 수집 기록 조회 실패';
      }
    } finally {
      refresh.disabled = false;
    }
  }

  container.querySelector('.dart-activity-filters').addEventListener('click', (event) => {
    const button = event.target.closest('[data-dart-filter]');
    if (!button) return;
    selectedFilter = button.dataset.dartFilter;
    container.querySelectorAll('[data-dart-filter]').forEach((item) => item.classList.toggle('active', item === button));
    render();
  });
  markets.addEventListener('click', (event) => {
    const button = event.target.closest('[data-dart-market]');
    if (!button) return;
    selectedMarket = button.dataset.dartMarket;
    renderMarkets();
    render();
  });
  refresh.addEventListener('click', load);
  load();
}
