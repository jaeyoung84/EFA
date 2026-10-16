import { marketRankingsView } from './todayGainers.js';

// EINT-049: 반도체 소부장 전 종목(네이버 테마 반도체 장비·재료/부품·기판 합집합) — 거래소 등락률 순.
export function todaySobujangView(container) {
  marketRankingsView(container, {
    id: 'sobujang',
    title: '금일 소부장 종목',
    icon: 'fa-microchip',
    description: '국내 반도체 소재·부품·장비 전 종목(네이버 증권 테마: 반도체 장비·반도체 재료/부품·반도체 기판, 약 170종목) 중 오늘 등락률이 높은 순서입니다. 기본은 상위 20개이고 "전체 보기"로 모든 종목을 볼 수 있습니다. 삼성전자·SK하이닉스 같은 완제품 업체는 포함하지 않습니다.',
    endpoint: '/api/market/top-sobujang',
    loadingText: '오늘의 소부장 등락률을 불러오는 중…',
    note: '네이버 증권 시세(장중 지연될 수 있음)와 거래소 등락률 기준이며, 장중에는 순위가 계속 바뀝니다. 테마 분류는 네이버 기준이라 공식 업종 분류와 다를 수 있습니다. 소부장 종목은 고객사 투자·반도체 업황·환율·수주 공시에 민감할 수 있으므로, 등락률만으로 투자 판단을 내리지 마세요.',
    allToggle: true,
  });
}
