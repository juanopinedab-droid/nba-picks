const BASE = '/api'

async function fetchJSON(url: string, options?: RequestInit) {
  const method = options?.method || 'GET'
  console.log(`[API] ${method} ${url}`)
  const res = await fetch(url, options)
  const json = await res.json()
  console.log(`[API] ${method} ${url} -> ${res.status}`, typeof json === 'object' ? Object.keys(json).join(',') : typeof json)
  return json
}

export const api = {
  health: () => fetchJSON(`${BASE}/health`),

  picks: {
    generate: (season?: string, minEdge?: number, fetchProps?: boolean, bankroll?: number) =>
      fetchJSON(`${BASE}/picks/nba`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ...(season ? { season } : {}),
          ...(minEdge !== undefined ? { min_edge: minEdge } : {}),
          ...(fetchProps !== undefined ? { fetch_props: fetchProps } : {}),
          ...(bankroll !== undefined ? { bankroll } : {}),
        }),
      }),
    status: () => fetchJSON(`${BASE}/picks/status`),
    football: {
      generate: (params?: Record<string, any>) =>
        fetchJSON(`${BASE}/picks/football`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(params || {}),
        }),
      status: () => fetchJSON(`${BASE}/picks/football/status`),
    },
    mlb: {
      generate: (params?: Record<string, any>) =>
        fetchJSON(`${BASE}/mlb/run`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(params || {}),
        }),
      status: () => fetchJSON(`${BASE}/mlb/status`),
      pending: () => fetchJSON(`${BASE}/mlb/pending`),
      history: () => fetchJSON(`${BASE}/mlb/history`),
    },
  },

  pending: {
    list: () => fetchJSON(`${BASE}/pending`),
    mark: (id: number, result: string) =>
      fetchJSON(`${BASE}/pending/${id}/result`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ result }),
      }),
  },

  history: () => fetchJSON(`${BASE}/history`),

  resolve: {
    run: (fecha?: string) =>
      fetchJSON(`${BASE}/resolve`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(fecha ? { fecha } : {}),
      }),
    status: () => fetchJSON(`${BASE}/resolve/status`),
  },

  close: {
    run: () => fetchJSON(`${BASE}/close`, { method: 'POST' }),
    status: () => fetchJSON(`${BASE}/close/status`),
  },

  backtest: {
    run: (seasons = 2, downloadOnly = false) =>
      fetchJSON(`${BASE}/backtest`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ seasons, download_only: downloadOnly }),
      }),
    status: () => fetchJSON(`${BASE}/backtest/status`),
  },

  calibrate: {
    run: (apply = false) =>
      fetchJSON(`${BASE}/calibrate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ apply }),
      }),
    status: () => fetchJSON(`${BASE}/calibrate/status`),
  },

  bankroll: {
    get: () => fetchJSON(`${BASE}/bankroll`),
    set: (bankroll: number) =>
      fetchJSON(`${BASE}/bankroll`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ bankroll }),
      }),
    deposit: (amount: number) =>
      fetchJSON(`${BASE}/bankroll/deposit`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ amount }),
      }),
    withdraw: (amount: number) =>
      fetchJSON(`${BASE}/bankroll/withdraw`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ amount }),
      }),
    history: (limit = 50) => fetchJSON(`${BASE}/bankroll/history?limit=${limit}`),
  },

  jobs: {
    status: (id: string) => fetchJSON(`${BASE}/jobs/${id}`),
    history: (limit = 20, jobType?: string) =>
      fetchJSON(`${BASE}/jobs?limit=${limit}${jobType ? `&type=${jobType}` : ''}`),
  },

  pm: {
    tags: () => fetchJSON(`${BASE}/pm/tags`),
    markets: (params?: Record<string, any>) => {
      const qs = new URLSearchParams()
      if (params?.tag) qs.set('tag', params.tag)
      if (params?.limit) qs.set('limit', String(params.limit))
      if (params?.min_volume) qs.set('min_volume', String(params.min_volume))
      return fetchJSON(`${BASE}/pm/markets?${qs.toString()}`)
    },
    scanner: {
      run: (params: Record<string, any>) =>
        fetchJSON(`${BASE}/pm/scanner`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(params),
        }),
      status: (jobId: string) => fetchJSON(`${BASE}/pm/scanner/status/${jobId}`),
    },
    portfolio: {
      get: () => fetchJSON(`${BASE}/pm/portfolio`),
      open: (market: any, analysis: any, fraction = 0.25) =>
        fetchJSON(`${BASE}/pm/portfolio/open`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ market, analysis, fraction }),
        }),
      close: (positionId: number, reason = 'manual') =>
        fetchJSON(`${BASE}/pm/portfolio/close`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ position_id: positionId, reason }),
        }),
    },
    history: () => fetchJSON(`${BASE}/pm/history`),
    treasury: {
      get: () => fetchJSON(`${BASE}/pm/treasury`),
      deposit: (amount: number, note = '') =>
        fetchJSON(`${BASE}/pm/treasury/deposit`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ amount, note }),
        }),
      withdraw: (amount: number, note = '') =>
        fetchJSON(`${BASE}/pm/treasury/withdraw`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ amount, note }),
        }),
      history: () => fetchJSON(`${BASE}/pm/treasury/history`),
    },
    keys: {
      get: () => fetchJSON(`${BASE}/pm/keys`),
      save: (data: Record<string, string>) =>
        fetchJSON(`${BASE}/pm/keys`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(data),
        }),
    },
    laboratory: {
      strategies: {
        list: () => fetchJSON(`${BASE}/pm/laboratory/strategies`),
        get: (id: number) => fetchJSON(`${BASE}/pm/laboratory/strategies/${id}`),
        save: (name: string, strategyType: string, params: Record<string, any>, isActive: boolean) =>
          fetchJSON(`${BASE}/pm/laboratory/strategies`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, strategy_type: strategyType, params, is_active: isActive ? 1 : 0 }),
          }),
        delete: (id: number) =>
          fetchJSON(`${BASE}/pm/laboratory/strategies/${id}`, { method: 'DELETE' }),
        update: (id: number, name: string, strategyType: string, params: Record<string, any>, isActive: boolean) =>
          fetchJSON(`${BASE}/pm/laboratory/strategies/${id}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, strategy_type: strategyType, params, is_active: isActive ? 1 : 0 }),
          }),
      },
      backtest: {
        run: (params: Record<string, any>) =>
          fetchJSON(`${BASE}/pm/laboratory/backtest`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(params),
          }),
      },
    },
    priceHistory: (slug: string, days = 7) =>
      fetchJSON(`${BASE}/pm/price-history?slug=${slug}&days=${days}`),
    aiResearch: {
      stream: (params: Record<string, any>) =>
        fetch(`${BASE}/pm/ai-research/stream`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(params),
        }),
      resume: (id: number) =>
        fetch(`${BASE}/pm/ai-research/${id}/resume`, {
          method: 'POST',
        }),
      cancel: (id: number) =>
        fetch(`${BASE}/pm/ai-research/${id}/cancel`, {
          method: 'POST',
        }),
      history: (limit = 30) =>
        fetchJSON(`${BASE}/pm/ai-research/history?limit=${limit}`),
      get: (id: number) =>
        fetchJSON(`${BASE}/pm/ai-research/${id}`),
    },
  },
}

export interface MispricingPick {
  outcome: string
  market_price: number
  fair_value: number
  edge_pct: number
  action: 'BUY' | 'SELL' | 'HOLD'
  rationale: string
}

export interface MispricingReport {
  summary: string
  picks: MispricingPick[]
}
