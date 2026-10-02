const BASE = '';

export async function apiFetch(path, options = {}) {
  const res = await fetch(BASE + path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || res.statusText);
  }
  return res.json();
}

export const api = {
  health:           ()      => apiFetch('/api/health'),
  systemResources:  ()      => apiFetch('/api/system/resources'),
  visitorHeartbeat: (body)  => apiFetch('/api/visitors/heartbeat',         { method: 'POST', body: JSON.stringify(body) }),
  portfolioCombination: (body) => apiFetch('/api/market/portfolio-combination', { method: 'POST', body: JSON.stringify(body) }),
  marketSnapshot:   (body)  => apiFetch('/api/market/snapshot',             { method: 'POST', body: JSON.stringify(body) }),
  macroRealtime:    (body)  => apiFetch('/api/macro/realtime',              { method: 'POST', body: JSON.stringify(body) }),
  dartCompanySearch:(body)  => apiFetch('/api/dart/company-search',         { method: 'POST', body: JSON.stringify(body) }),
  dartMarketActivity: ()  => apiFetch('/api/dart/market-activity'),
  industrySector:   (body)  => apiFetch('/api/industry/sector',             { method: 'POST', body: JSON.stringify(body) }),
  industryPeer:     (body)  => apiFetch('/api/industry/peer',               { method: 'POST', body: JSON.stringify(body) }),
  companyFinancials:(body)  => apiFetch('/api/finance/company-financials',  { method: 'POST', body: JSON.stringify(body) }),
  dartFinancialAnalysis: (body) => apiFetch('/api/dart/financial-analysis', { method: 'POST', body: JSON.stringify(body) }),
};
