const NOTES = {
  synthetic: {
    icon: 'fa-flask',
    color: '#f59e0b',
    text: '이 화면은 학습용으로 만든 합성(가상) 데이터를 사용합니다. 실제 시세나 투자 성과가 아니므로 투자 판단의 근거로 사용하지 마세요.',
  },
  real: {
    icon: 'fa-database',
    color: '#0ea5e9',
    text: '이 화면은 외부 제공자(Yahoo Finance 등)의 실제 데이터를 사용합니다. 지연되거나 일부 누락될 수 있습니다.',
  },
  dart: {
    icon: 'fa-database',
    color: '#0ea5e9',
    text: '이 화면은 OpenDART 공시·시장 데이터를 사용합니다. 실제 데이터이며, 공시·갱신 주기에 따라 최신 상태와 다를 수 있습니다.',
  },
};

/**
 * @param {keyof typeof NOTES} kind
 * @param {string} [customText] - 화면별로 더 구체적인 안내가 필요하면 기본 문구 대신 사용
 * @returns {string} HTML — 실제/지연/가상 데이터 여부를 알리는 공통 배너
 */
export function dataSourceNote(kind, customText) {
  const meta = NOTES[kind];
  if (!meta) return '';
  return `
    <p style="margin:0 0 16px;padding:10px 13px;background:${meta.color}14;border:1px solid ${meta.color}55;border-radius:9px;color:${meta.color};font-size:.74rem;line-height:1.6;">
      <i class="fa-solid ${meta.icon}"></i> ${customText || meta.text}
    </p>`;
}
