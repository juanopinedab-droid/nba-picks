import { useCallback, useEffect, useState } from 'react'
import { PolymarketSidebar } from './PolymarketSidebar'
import { PolymarketHeader } from './PolymarketHeader'
import { ScannerPage } from './pages/Scanner'
import { PortfolioPage } from './pages/Portfolio'
import { HistoryPage } from './pages/History'
import { AIResearchPage } from './pages/AIResearch'
import { api } from '@/lib/api'

interface PolymarketShellProps {
  workspace: 'nba' | 'polymarket'
  onSwitchWorkspace: (ws: 'nba' | 'polymarket') => void
}

export function PolymarketShell({ workspace, onSwitchWorkspace }: PolymarketShellProps) {
  const [activeTab, setActiveTab] = useState(() => {
    const hash = window.location.hash.replace(/^#\/pm\//, '')
    return hash || 'scanner'
  })
  const [balance, setBalance] = useState(0)
  const [record, setRecord] = useState({ wins: 0, losses: 0 })
  const [sidebarOpen, setSidebarOpen] = useState(false)

  useEffect(() => {
    const handler = () => {
      const hash = window.location.hash.replace(/^#\/pm\//, '')
      if (hash && ['scanner', 'portfolio', 'history', 'ai-research'].includes(hash)) {
        setActiveTab(hash)
      }
    }
    window.addEventListener('hashchange', handler)
    return () => window.removeEventListener('hashchange', handler)
  }, [])

  const navigate = useCallback((tab: string) => {
    window.location.hash = `#/pm/${tab}`
  }, [])

  const loadHeader = useCallback(() => {
    api.pm.treasury.get().then(d => {
      setBalance(d.balance_usd || 0)
    }).catch(() => {})
    api.pm.history().then(d => {
      const positions = d.positions || []
      const wins = positions.filter((p: any) => (p.pnl_usd || 0) > 0).length
      const losses = positions.filter((p: any) => (p.pnl_usd || 0) < 0).length
      setRecord({ wins, losses })
    }).catch(() => {})
  }, [])

  useEffect(() => {
    loadHeader()
    const interval = setInterval(() => loadHeader(), 30000)
    return () => clearInterval(interval)
  }, [loadHeader])

  return (
    <div className="min-h-screen bg-page">
      <PolymarketSidebar
        activeTab={activeTab}
        onTabChange={navigate}
        workspace={workspace}
        onSwitchWorkspace={onSwitchWorkspace}
        sidebarOpen={sidebarOpen}
        onToggleSidebar={() => setSidebarOpen(!sidebarOpen)}
      />
      <div className="lg:ml-[220px] ml-0 flex flex-col min-h-screen">
        <PolymarketHeader
          title={activeTab}
          balance={balance}
          wins={record.wins}
          losses={record.losses}
          onRefresh={loadHeader}
          onToggleSidebar={() => setSidebarOpen(!sidebarOpen)}
        />
        <main className="flex-1 px-6 py-6 animate-content-in">
          {activeTab === 'scanner' && <ScannerPage />}
          {activeTab === 'portfolio' && <PortfolioPage onUpdateHeader={loadHeader} />}
          {activeTab === 'history' && <HistoryPage />}
          {activeTab === 'ai-research' && <AIResearchPage />}
        </main>
      </div>
    </div>
  )
}
