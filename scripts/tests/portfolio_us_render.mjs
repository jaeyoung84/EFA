// Codex 수정: CODX-USPORT-001 (2026-10-12), 한국·미국 합산 및 뉴스·SEC 화면 회귀.
import assert from 'node:assert/strict';

globalThis.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
const moduleUrl = new URL('../../app/frontend/js/views/portfolioHoldings.js', import.meta.url).href;
const { portfolioInsights, portfolioUpdatesHtml } = await import(moduleUrl);

const insight = portfolioInsights(
  { cash_krw: 100_000 },
  { value_krw: 1_080_000, positions: [
    { ticker: '005930.KS', name: '삼성전자', market: 'KR', currency: 'KRW', value_krw: 140_000 },
    { ticker: 'AAPL', name: 'Apple', market: 'US', currency: 'USD', value_krw: 840_000 },
  ] },
);
assert.equal(insight.total, 1_080_000);
assert.equal(insight.currency.KRW, 240_000);
assert.equal(insight.currency.USD, 840_000);
assert.equal(Math.round(insight.positions.reduce((sum, item) => sum + item.weight_pct, 0)), 100);

const html = portfolioUpdatesHtml(
  { items: [
    { market: 'KR', name: '삼성전자', ticker: '005930.KS', title: '국내 뉴스', source: '연합뉴스', url: 'https://news.example/kr' },
    { market: 'US', name: 'Apple', ticker: 'AAPL', title: 'US news', source: 'Reuters', url: 'https://news.example/us' },
  ] },
  { items: [
    { market: 'US', name: 'Apple', ticker: 'AAPL', form: '10-Q', title: 'Quarterly report', filed_at: '2026-08-01', url: 'https://www.sec.gov/aapl' },
  ] },
);
assert.match(html, /한국·미국 뉴스/);
assert.match(html, /미국 SEC 제출자료/);
assert.match(html, /10-Q/);
assert.match(html, /rel="noopener noreferrer"/);
assert.doesNotMatch(
  portfolioUpdatesHtml({ items: [{ market: 'US', title: '<script>x<\/script>', url: 'javascript:alert(1)' }] }, {}),
  /<script>/,
);

const errorHtml = portfolioUpdatesHtml({ error: 'news down' }, { error: 'sec down' });
assert.match(errorHtml, /뉴스를 불러오지 못했습니다/);
assert.match(errorHtml, /SEC 자료를 불러오지 못했습니다/);
console.log('portfolio_us_render: ok');
