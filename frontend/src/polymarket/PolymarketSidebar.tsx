import { ScanSearch, Briefcase, History, Landmark, Brain } from 'lucide-react'
import { GenericSidebar } from '@/components/GenericSidebar'
import type { SidebarItem } from '@/components/GenericSidebar'

interface SidebarProps {
  activeTab: string
  onTabChange: (tab: string) => void
  workspace: 'nba' | 'polymarket'
  onSwitchWorkspace: (ws: 'nba' | 'polymarket') => void
  sidebarOpen: boolean
  onToggleSidebar: () => void
}

const items: SidebarItem[] = [
  { id: 'scanner',     label: 'Scanner',     icon: ScanSearch },
  { id: 'portfolio',   label: 'Portfolio',   icon: Briefcase },
  { id: 'history',     label: 'History',     icon: History },
  { id: 'ai-research', label: 'AI Research', icon: Brain },
]

export function PolymarketSidebar(props: SidebarProps) {
  return (
    <GenericSidebar
      {...props}
      items={items}
      logo={{ icon: Landmark, title: 'Polymarket' }}
    />
  )
}
