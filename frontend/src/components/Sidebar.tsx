import {
  LayoutDashboard, Circle, Clock, BarChart3,
  TrendingUp, Gauge, Wallet, HelpCircle,
  Diamond,
} from 'lucide-react'
import { GenericSidebar } from './GenericSidebar'
import type { SidebarItem } from './GenericSidebar'

interface SidebarProps {
  activeTab: string
  onTabChange: (tab: string) => void
  workspace: 'nba' | 'polymarket'
  onSwitchWorkspace: (ws: 'nba' | 'polymarket') => void
  sidebarOpen: boolean
  onToggleSidebar: () => void
}

const items: SidebarItem[] = [
  { id: 'picks',     label: 'NBA',         icon: LayoutDashboard },
  { id: 'football',  label: 'Futbol',      icon: Circle },
  { id: 'mlb',       label: 'MLB',         icon: Diamond },
  { id: 'pending',   label: 'Pendientes',  icon: Clock },
  { id: 'history',   label: 'Historial',   icon: BarChart3 },
  { id: 'backtest',  label: 'Backtest',    icon: TrendingUp },
  { id: 'calibrate', label: 'Calibracion', icon: Gauge },
  { id: 'bankroll',  label: 'Bankroll',    icon: Wallet },
  { id: 'help',      label: 'Ayuda',       icon: HelpCircle },
]

export function Sidebar(props: SidebarProps) {
  return (
    <GenericSidebar
      {...props}
      items={items}
      logo={{ icon: LayoutDashboard, title: 'NBA Picks Bot' }}
    />
  )
}
