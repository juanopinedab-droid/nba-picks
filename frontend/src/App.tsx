import { useState, useEffect, useCallback, useRef } from 'react'
import { Sidebar } from './components/Sidebar'
import { Header } from './components/Header'
import { PolymarketShell } from './polymarket/PolymarketShell'
import { PicksPage } from './pages/Picks'
import { FootballPage } from './pages/Football'
import { MlbPicksPage } from './pages/MlbPicks'
import { PendingPage } from './pages/Pending'
import { HistoryPage } from './pages/History'
import { BacktestPage } from './pages/Backtest'
import { CalibratePage } from './pages/Calibrate'
import { BankrollPage } from './pages/Bankroll'
import { HelpPage } from './pages/Help'
import { api } from './lib/api'
import { fmt } from './lib/utils'
import { JobProvider } from './lib/JobsContext'

function useHashRouter(defaultTab: string, workspace: string) {
  const prefix = workspace === 'polymarket' ? 'pm' : 'sports'
  const getTab = useCallback(() => {
    const hash = window.location.hash.replace(/^#\/?/, '')
    return hash.replace(new RegExp(`^${prefix}/`), '') || defaultTab
  }, [defaultTab, prefix])

  const [tab, setTab] = useState(getTab)

  useEffect(() => {
    const handler = () => setTab(getTab())
    window.addEventListener('hashchange', handler)
    return () => window.removeEventListener('hashchange', handler)
  }, [getTab])

  const navigate = useCallback((t: string) => {
    window.location.hash = `#/${prefix}/${t}`
  }, [prefix])

  return [tab, navigate] as const
}

function App() {
  const [workspace, setWorkspace] = useState<'nba' | 'polymarket'>(() => {
    const hash = window.location.hash
    return hash.startsWith('#/pm/') ? 'polymarket' : 'nba'
  })
  const [activeTab, setActiveTab] = useHashRouter(workspace === 'polymarket' ? 'scanner' : 'picks', workspace)
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [header, setHeader] = useState({ bankroll: '$— COP', record: '—', bankrollValue: 0 })
  const healthTimer = useRef<any>(null)

  const updateHeader = (d: any) => {
    const br = d.bankroll || 0
    const { wins = 0, losses = 0 } = d.record || {}
    const total = wins + losses
    const winPct = total ? ((wins / total) * 100).toFixed(0) + '%' : '—'
    setHeader({
      bankroll: `$${fmt(br)} COP`,
      record: `${wins}W-${losses}L (${winPct})`,
      bankrollValue: br,
    })
  }

  const loadHealth = useCallback((retries = 5) => {
    if (workspace === 'polymarket') return
    api.health().then(d => {
      updateHeader(d)
    }).catch(() => {
      if (retries > 1) {
        setTimeout(() => loadHealth(retries - 1), 2000)
      }
    })
  }, [workspace])

  useEffect(() => {
    loadHealth()
    return () => clearInterval(healthTimer.current)
  }, [loadHealth])

  useEffect(() => {
    if (workspace === 'nba') {
      healthTimer.current = setInterval(() => loadHealth(0), 30000)
    }
    return () => clearInterval(healthTimer.current)
  }, [loadHealth, workspace])

  const handleWorkspaceSwitch = (ws: 'nba' | 'polymarket') => {
    setWorkspace(ws)
    window.location.hash = ws === 'polymarket' ? '#/pm/scanner' : '#/sports/picks'
  }

  if (workspace === 'polymarket') {
    return (
      <PolymarketShell
        workspace={workspace}
        onSwitchWorkspace={handleWorkspaceSwitch}
      />
    )
  }

  return (
    <JobProvider>
      <div className="min-h-screen bg-page">
        <Sidebar
          activeTab={activeTab}
          onTabChange={setActiveTab}
          workspace={workspace}
          onSwitchWorkspace={handleWorkspaceSwitch}
          sidebarOpen={sidebarOpen}
          onToggleSidebar={() => setSidebarOpen(!sidebarOpen)}
        />
        <div className="lg:ml-[220px] ml-0 flex flex-col min-h-screen">
          <Header
            title={activeTab}
            bankroll={header.bankroll}
            record={header.record}
            bankrollValue={header.bankrollValue}
            onRefresh={loadHealth}
            onToggleSidebar={() => setSidebarOpen(!sidebarOpen)}
          />
          <main className="flex-1 px-6 py-6 animate-content-in">
            {activeTab === 'mlb' && <MlbPicksPage />}
            {activeTab === 'picks' && <PicksPage />}
            {activeTab === 'football' && <FootballPage />}
            {activeTab === 'pending' && <PendingPage />}
            {activeTab === 'history' && <HistoryPage />}
            {activeTab === 'backtest' && <BacktestPage />}
            {activeTab === 'calibrate' && <CalibratePage />}
            {activeTab === 'bankroll' && <BankrollPage />}
            {activeTab === 'help' && <HelpPage />}
          </main>
        </div>
      </div>
    </JobProvider>
  )
}

export default App
