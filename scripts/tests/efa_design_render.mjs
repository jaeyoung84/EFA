// EFA 디자인 개편: 대시보드·상단 알림·관심종목·리서치 노트의 순수 렌더 함수 검사(실데이터 없음 처리·escape·트리맵).
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const STUBS = {
  authRequest: 'async () => ({})', isAuthenticated: '() => true', openAuthModal: '() => {}', currentUserInfo: '() => null',
  portfolioInsights: '() => ({})', portfolioValuation: 'async () => ({})',
  DECISION_LABEL: "{ buy: ['매수', 'is-hold'], hold: ['관망', 'is-muted'], exclude: ['제외', 'is-muted'] }",
  decisionForm: `(pick) => '<form data-sb-decision data-signal-id="' + pick.signal_id + '"></form>'`, bindDecisionForm: '() => {}',
  attachStockSearch: '() => {}', PENDING_STOCK_KEY: "'k'",
  sectorSlot: '(s) => `<span data-sector-symbol="${s}"></span>`', fillSectorBadges: '() => {}',
  liveSlot: '(s, m) => `<span data-live-ticker="${s}.${m}"></span>`', startLiveQuotes: '() => {}', fetchLiveQuotes: 'async () => ({})',
  riskSlot: "(s, o) => `<span data-risk-symbol=\"${s}\"${o && o.full ? ' data-risk-full' : ''}></span>`", fillRiskBadges: '() => {}',
  TIME_FORMAT: "{ timeZone: 'Asia/Seoul', hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' }",
};
async function load(file) {
  const source = (await readFile(new URL(`../../app/frontend/js/${file}`, import.meta.url), 'utf8')).replace(/^import .*$/gm, '');
  // 파일이 직접 선언하지 않은 import 이름만 가짜 구현으로 채운다.
  const stubs = Object.entries(STUBS).filter(([name]) => !new RegExp(`(const|function)\\s+${name}\\b`).test(source))
    .map(([name, code]) => `const ${name} = ${code};`).join('\n');
  return import(`data:text/javascript;base64,${Buffer.from(`${stubs}\n${source}`).toString('base64')}`);
}

const dash = await load('views/dashboard.js');
const shell = await load('shell.js');
const watch = await load('views/watchlist.js');
const notes = await load('views/researchNotes.js');
const XSS = '<img src=x onerror=alert(1)>';

// 인사: 시간대별 문구, 이름 escape
assert.ok(dash.greetingHtml(new Date('2026-10-01T00:30:00Z'), XSS).includes('좋은 아침입니다'));  // 09:30 KST
assert.ok(dash.greetingHtml(new Date('2026-10-01T10:00:00Z'), '').includes('좋은 저녁입니다, 투자자님'));
assert.ok(!dash.greetingHtml(new Date(), XSS).includes('<img'));

// 브리핑: 로그인 전·추천 없음·판단 배지·escape
const board = { available: true, as_of: '2026-09-30', expires_at: '2026-10-01T21:00:00Z',
  items: [{ item: 'pullback_v4', picks: [{ signal_id: 's1', rank: 1, name: XSS, symbol: '010170', close: 16680, reasons: ['r1'], reason_labels: ['상승 추세'] },
    { signal_id: 's2', rank: 2, name: '우리로', symbol: '046970', close: 7420, reasons: [], reason_labels: [] }] }],
  my_decisions: { s2: [{ decision: 'hold' }] }, sell_signals: [], health: { level: 'ok', alerts: [], published_session: '2026-09-30' } };
assert.ok(dash.briefingHtml('decisions', { loggedIn: false }).includes('data-dash-login'));
const brief = dash.briefingHtml('decisions', { board, loggedIn: true });
assert.ok(!brief.includes('<img') && brief.includes('판단 대기') && brief.includes('관망') && brief.includes('상승 추세'));
assert.ok(dash.briefingHtml('decisions', { board: { ...board, items: [] }, loggedIn: true }).includes('조건을 만족한 종목이 없습니다'));
assert.ok(dash.briefingHtml('sells', { board, loggedIn: true }).includes('기록된 보유 종목이 없습니다'));
assert.ok(dash.briefingHtml('outcomes', { board, outcomes: { available: false, detail: '아직' }, loggedIn: true }).includes('아직'));
assert.ok(dash.briefingHtml('health', { board, loggedIn: true }).includes('엔진 정상'));

// 의사결정 큐: 필터·만료·버튼
const queue = dash.queueHtml({ board, loggedIn: true }, new Date('2026-10-01T08:30:00Z').getTime());
assert.equal((queue.match(/dash-queue-card/g) || []).length, 2);
assert.ok(queue.includes('만료 12시간 30분') && queue.includes('data-signal-id="s1"') && !queue.includes('<img'));
assert.equal((dash.queueHtml({ board, loggedIn: true, filter: 'waiting' }).match(/dash-queue-card/g) || []).length, 1);
assert.ok(dash.queueHtml({ board, loggedIn: true, filter: 'decided' }).includes('우리로'));

// 트리맵: 면적 합 100%, 0 이하 제외, 겹침 없음
const tiles = dash.treemapLayout([{ value: 50 }, { value: 30 }, { value: 20 }, { value: 0 }]);
assert.equal(tiles.length, 3);
assert.ok(Math.abs(tiles.reduce((sum, t) => sum + t.w * t.h, 0) - 10000) < 1e-6);
tiles.forEach((t) => assert.ok(Math.abs(t.w * t.h / 100 - t.value) < 1e-6));
assert.ok(dash.assetsHtml({ loggedIn: true, insight: { total: 0, positions: [] } }).includes('보유자산 등록'));

// 시장 흐름·이동평균·시스템 상태: 데이터 없으면 '-'
assert.equal(dash.sparklinePath([1]), '');
assert.ok(dash.sparklinePath([1, 2, 3]).startsWith('M0.0,'));
assert.deepEqual(dash.movingAverage([1, 2, 3, 4], 2), [null, 1.5, 2.5, 3.5]);
const card = dash.flowCardHtml({ name: 'KOSPI', digits: 2 }, null, { ohlcv: [], error: '차트 데이터 없음: x' });
assert.ok(card.includes('시세 조회 실패') && card.includes('차트 데이터 없음'));
const rows = dash.systemRows({ refresh: null, health: null, outcomes: null, quotesAt: null, loggedIn: false });
assert.ok(rows.every(([, status]) => status === 'unknown') && rows.length === 3);

// 상단 알림
assert.ok(shell.bellPanelHtml(null, false).includes('data-bell-login'));
assert.ok(shell.bellPanelHtml({ alerts: [], published_session: '2026-09-30' }, true).includes('알림 없음'));
const alertPanel = shell.bellPanelHtml({ alerts: [{ level: 'error', title: XSS, message: 'm' }] }, true);
assert.ok(alertPanel.includes('is-error') && !alertPanel.includes('<img'));

// 관심종목·노트
assert.ok(watch.watchlistHtml([]).includes('관심종목이 없습니다'));
const watchRows = watch.watchlistHtml([{ ticker: '010170.KQ', code: '010170', name: XSS, market: 'KOSDAQ', memo: '', added_at: '2026-10-01T00:00:00Z' }],
  { '010170.KQ': { status: 'ok', value: 16680, change_pct: -1.2 } });
assert.ok(watchRows.includes('is-down') && watchRows.includes('-1.20%') && !watchRows.includes('<img'));
assert.ok(notes.notesListHtml([]).includes('노트가 없습니다'));
const noteRows = notes.notesListHtml([{ id: 'n1', title: XSS, body: 'b'.repeat(100), tickers: [{ name: '삼성전자' }], tags: ['AI'], updated_at: '2026-10-01T00:00:00Z' }], 'n1');
assert.ok(noteRows.includes('is-active') && noteRows.includes('#AI') && noteRows.includes('…') && !noteRows.includes('<img'));

// 추천 종목 섹터 배지
const badges = await load('utils/sectorBadges.js');
const leading = badges.sectorBadgeHtml({ sector_name: '반도체', quadrant: 'leading', quadrant_4w_ago: 'improving', ksic: '2612', rotation_as_of: '2026-09-30' });
assert.ok(leading.includes('<b>반도체</b>') && leading.includes('>주도</i>') && leading.includes('(개선→주도)'));
assert.ok(badges.sectorBadgeHtml({ sector_name: null, note: '업종 분류 없음', ksic: '64992' }).includes('업종 분류 없음'));
assert.ok(!badges.sectorBadgeHtml({ sector_name: '<img src=x>', quadrant: 'lagging' }).includes('<img'));
assert.ok(badges.sectorSlot('010170').includes('data-sector-symbol="010170"'));
assert.ok(queue.includes('data-sector-symbol="010170"'));

// 금일 상승종목(코스피·코스닥 전 종목)
const movers = await load('views/todayGainers.js');
const moverRow = movers.moverRowMarkup({ rank: 1, ticker: '217590.KQ', code: '217590', name: XSS, price: 18440, change_pct: 29.95,
  limit_up: true, trading_value_krw: 93_694_000_000, market_cap_krw: 444_300_000_000 });
assert.ok(moverRow.includes('상한가') && moverRow.includes('거래대금 937억') && moverRow.includes('시총 4,443억') && !moverRow.includes('<img'));
const moverStamp = movers.moverStampText({ market_status: 'OPEN', as_of: '2026-10-02T10:22:17+09:00', total_count: 1824, stale: false });
assert.ok(moverStamp.startsWith('장중') && moverStamp.includes('10:22 체결 기준'));
assert.ok(movers.moverStampText({ market_status: 'CLOSE', stale: true }).includes('이전 값'));

// 포트폴리오: 비중·팩터 표는 최대 20종목 + 표시 개수 안내
const holdings = await load('views/portfolioHoldings.js');
assert.equal(holdings.listedNote(20, 25), '종목 25개 중 상위 20개 표시 (최대 20개)');
assert.equal(holdings.listedNote(8, 8), '종목 8개 전체 표시 (최대 20개)');
const many = Array.from({ length: 25 }, (_, i) => ({ ticker: `T${i}`, name: `종목${i}`, value_krw: 100 - i, weight_pct: 4 - i * 0.1 }));
const weights = holdings.weightListHtml([...many, { ticker: 'X', name: '시세없음', weight_pct: NaN }]);
assert.equal((weights.match(/<li>/g) || []).length, 20);
assert.ok(weights.includes('자산(현금 포함) 25개 중 상위 20개 표시') && weights.includes('시세 없음 1개 제외'));
const factor = holdings.factorExposureHtml({ available: true, as_of: '2026-10-01', coverage_pct: 90, weighted_factors: {}, disclaimer: '',
  positions: [{ ticker: 'A.KS', name: '에이', weight_pct: 100, factors: { return_20d: 0.1 } }],
  excluded: [{ ticker: 'B.KQ', reason: '공통 기준일의 일봉이 없어 비교에서 제외했습니다.' }] }, { 'B.KQ': '<b>비</b>' });
assert.ok(factor.includes('종목 2개 전체 표시') && factor.includes('팩터 데이터 없음') && factor.includes('&lt;b&gt;비'));
const insight = holdings.portfolioInsights({ cash_krw: 0 }, { value_krw: 300, positions: [{ ticker: 'N', value_krw: undefined }, { ticker: 'S', value_krw: 100 }, { ticker: 'L', value_krw: 200 }] });
assert.deepEqual(insight.positions.map((p) => p.ticker), ['L', 'S', 'N']);
assert.equal(insight.max_position.ticker, 'L');
const partial = holdings.weightBasis({ cash_krw: 100 }, { available: false, positions: [{ status: 'ok', value_krw: 300 }, { status: 'error' }] });
assert.deepEqual([partial.partial, partial.priced, partial.excluded, partial.valuation.value_krw], [true, 1, 1, 400]);
assert.equal(holdings.weightBasis({}, { available: true, priced_count: 3, value_krw: 9 }).partial, false);

// 국내 종목 현재가(한투 체결가 / 지연 시세)
const live = await load('utils/liveQuotes.js');
assert.equal(live.liveTicker('005930', 'KOSPI'), '005930.KS');
assert.equal(live.liveTicker('0091p0', 'KOSDAQ'), '0091P0.KQ');
assert.equal(live.liveTicker('AAPL', 'NYSE'), '');
assert.ok(live.liveSlot('247540', 'KOSDAQ').includes('data-live-ticker="247540.KQ"'));
const liveHtml = live.liveQuoteHtml({ status: 'ok', price: 72000, change_pct: 1.234, source: 'kis_realtime', source_label: '한투 실시간 체결', quoted_at: '2026-10-02T10:15:00+09:00' });
assert.ok(liveHtml.includes('72,000') && liveHtml.includes('is-up') && liveHtml.includes('+1.23%') && liveHtml.includes('한투 실시간 체결 10:15:00'));
const fallbackHtml = live.liveQuoteHtml({ status: 'ok', price: 1, change_pct: -1, source: 'yahoo_delayed', source_label: 'Yahoo 지연 시세', fallback_reason: XSS });
assert.ok(fallbackHtml.includes('is-down') && fallbackHtml.includes('Yahoo 지연 시세') && !fallbackHtml.includes('<img'));
assert.ok(live.liveQuoteHtml({ status: 'error', reason: XSS }).includes('조회 실패') && !live.liveQuoteHtml({ status: 'error', reason: XSS }).includes('<img'));
assert.ok(queue.includes('data-live-ticker="010170.'));
const kisWatch = watch.watchlistHtml([{ ticker: '005930.KS', code: '005930', name: '삼성전자', market: 'KOSPI' }],
  { '005930.KS': { status: 'ok', price: 72000, change_pct: 0.5, source_label: '한투 실시간 체결' } }, '2026-10-02T01:15:00Z');
assert.ok(kisWatch.includes('72,000') && kisWatch.includes('현재가 출처: 한투 실시간 체결') && !kisWatch.includes('15분 지연'));
assert.ok(watch.watchlistHtml([{ ticker: 'X.KS', code: 'X', name: 'x' }], {}).includes('…'));
assert.equal(holdings.valuationSourceText({ source: 'market_data_cache_daily', positions: [{ quoted_at: '2026-10-01T00:00:00+00:00' }] }), '검증 캐시 · 일봉 종가 · 기준일 2026-10-01');
const kisNote = holdings.valuationSourceText({ source: 'kis_live', live_refresh_seconds: 3, positions: [{ quoted_at: '2026-10-02T01:15:00Z' }] });
assert.ok(kisNote.startsWith('한투 체결가(장중 실시간)') && kisNote.includes('10:15:00') && kisNote.endsWith('3초마다 갱신'));

// 추천 종목 위험 공시(EINT-025)
const risk = await load('utils/riskBadges.js');
assert.ok(risk.riskSlot('010170').includes('data-risk-symbol="010170"') && risk.riskSlot('1', { full: true }).includes('data-risk-full'));
const riskItem = { symbol: '010170', level: 'high', window_days: 30, checked: ['dart', 'krx'], errors: {}, flags: [
  { source: 'krx', severity: 'high', label: '투자경고', title: '거래소 지정 상태: 투자경고' },
  { source: 'dart', severity: 'caution', label: '전환사채(CB) 발행', title: XSS, date: '2026-09-25', url: 'https://dart.fss.or.kr/dsaf001/main.do?rcpNo=9' }] };
const compactRisk = risk.riskBadgeHtml(riskItem);
assert.ok(compactRisk.includes('is-high') && compactRisk.includes('위험 신호 2건') && !compactRisk.includes('<img'));
const fullRisk = risk.riskBadgeHtml(riskItem, { full: true });
assert.ok(fullRisk.startsWith('<details') && fullRisk.includes('rcpNo=9') && fullRisk.includes('rel="noopener noreferrer"') && !fullRisk.includes('<img'));
assert.ok(risk.riskBadgeHtml({ ...riskItem, level: 'caution', flags: [riskItem.flags[1]] }).includes('주의 신호 1건'));
assert.ok(risk.riskBadgeHtml({ ...riskItem, level: 'none', flags: [] }).includes('위험 신호 없음(30일)'));
assert.ok(risk.riskBadgeHtml({ ...riskItem, level: 'none', flags: [], checked: ['dart'], errors: { krx: 'x' } }).includes('거래소 지정 미확인'));
assert.ok(risk.riskBadgeHtml(null).includes('확인 실패'));
assert.equal(risk.riskBadgeHtml({ ...riskItem, level: 'none', flags: [] }, { full: true, hideClean: true }), '');
assert.ok(risk.riskBadgeHtml({ ...riskItem, level: 'none', flags: [] }, { full: true }).includes('위험 신호 없음'));
assert.ok(risk.riskSlot('1', { full: true, hideClean: true }).includes('data-risk-hide-clean'));
assert.ok(queue.includes('data-risk-symbol="010170" data-risk-full'));

// 재무 차트: x축 라벨 간격(좁으면 건너뛰기)
const finSource = (await readFile(new URL('../../app/frontend/js/utils/financialCharts.js', import.meta.url), 'utf8'));
const fin = await import(`data:text/javascript;base64,${Buffer.from(`const window = {};\n${finSource}`).toString('base64')}`);
assert.equal(fin.labelStep(30, 100), 1);
assert.equal(fin.labelStep(38, 45), 2);
assert.equal(fin.labelStep(40, 10), 5);
assert.equal(fin.labelStep(40, 0), 1);

console.log('EFA design: greeting, briefing, queue, treemap, flow, system, bell, watchlist, notes, financial label spacing, market movers, holdings lists, live quotes, risk badges passed');
