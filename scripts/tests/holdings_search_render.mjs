// 포트폴리오 입력 자동 연결 규칙: exact 표시가 티커 하나면 연결, 둘 이상(GOOGL·GOOG)이면 목록 선택, 없으면 이름 완전 일치.
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const source = await readFile(new URL('../../app/frontend/js/views/portfolioHoldings.js', import.meta.url), 'utf8');
const match = source.match(/export function holdingExactMatch[\s\S]*?\n}\n/);
assert.ok(match, 'holdingExactMatch not found');
const { holdingExactMatch } = await import(`data:text/javascript;base64,${Buffer.from(match[0]).toString('base64')}`);
const norm = (v) => String(v || '').trim().toLocaleLowerCase().replace(/\s+/g, '');
assert.equal(holdingExactMatch([{ ticker: 'TSLA', name: 'Tesla, Inc.', exact: true }, { ticker: 'TSLL', name: 'x' }], 'tesla', norm).ticker, 'TSLA');
assert.equal(holdingExactMatch([{ ticker: 'GOOGL', name: 'Alphabet Inc.', exact: true }, { ticker: 'GOOG', name: 'Alphabet Inc.', exact: true }], 'alphabet', norm), null);
assert.equal(holdingExactMatch([{ ticker: '005930.KS', name: '삼성전자' }], '삼성전자', norm).ticker, '005930.KS');
assert.equal(holdingExactMatch([{ ticker: 'X', name: 'Other' }], 'tesla', norm), null);
console.log('holdings_search_render ok');
