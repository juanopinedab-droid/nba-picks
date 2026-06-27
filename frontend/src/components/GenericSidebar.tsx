import { Sun, Moon, Menu, X } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { useTheme } from '@/lib/theme'
import { WorkspaceSwitcher } from './WorkspaceSwitcher'

export interface SidebarItem {
  id: string
  label: string
  icon: LucideIcon
}

interface GenericSidebarProps {
  activeTab: string
  onTabChange: (tab: string) => void
  workspace: 'nba' | 'polymarket'
  onSwitchWorkspace: (ws: 'nba' | 'polymarket') => void
  items: SidebarItem[]
  logo: { icon: LucideIcon; title: string }
  sidebarOpen: boolean
  onToggleSidebar: () => void
}

export function GenericSidebar({
  activeTab,
  onTabChange,
  workspace,
  onSwitchWorkspace,
  items,
  logo: { icon: LogoIcon, title: logoTitle },
  sidebarOpen,
  onToggleSidebar,
}: GenericSidebarProps) {
  const { theme, toggle } = useTheme()
  const footerDelay = items.length * 0.03 + 0.2

  const sidebarContent = (
    <aside className="fixed left-0 top-0 h-screen w-[220px] bg-sidebar border-r border-sidebar-border flex flex-col z-30">
      <div className="flex items-center gap-2.5 px-5 h-14 border-b border-sidebar-border animate-fade-in">
        <LogoIcon className="w-5 h-5 text-accent" />
        <span className="font-bold text-base text-foreground flex-1">
          {logoTitle}
        </span>
        <button onClick={onToggleSidebar} className="lg:hidden text-muted hover:text-foreground">
          <X className="w-4 h-4" />
        </button>
      </div>

      <nav className="flex-1 py-3 px-3 space-y-0.5">
        {items.map((item, i) => {
          const active = activeTab === item.id
          return (
            <button
              key={item.id}
              onClick={() => { onTabChange(item.id); onToggleSidebar() }}
              style={{ '--item-delay': `${i * 0.03}s` } as React.CSSProperties}
              className={`animate-sidebar-item w-full flex items-center gap-3 py-2.5 px-3 rounded-lg text-sm font-medium transition-colors ${
                active
                  ? 'bg-sidebar-active text-accent border-l-[3px] border-accent pl-[9px]'
                  : 'text-muted hover:text-foreground hover:bg-sidebar-active border-l-[3px] border-transparent pl-[9px]'
              }`}
            >
              <item.icon className="w-5 h-5" />
              {item.label}
            </button>
          )
        })}
      </nav>

      <div
        className="px-3 pb-3 space-y-2 animate-fade-in"
        style={{ animationDelay: `${footerDelay}s` }}
      >
        <button
          onClick={toggle}
          className="w-full flex items-center justify-center gap-2 px-3 py-2.5 rounded-lg text-sm text-muted hover:text-foreground hover:bg-sidebar-active transition-colors"
        >
          {theme === 'light' ? (
            <Moon className="w-5 h-5" />
          ) : (
            <Sun className="w-5 h-5" />
          )}
          {theme === 'light' ? 'Oscuro' : 'Claro'}
        </button>

        <WorkspaceSwitcher workspace={workspace} onSwitch={onSwitchWorkspace} />
      </div>
    </aside>
  )

  return (
    <>
      <div className="hidden lg:block">
        {sidebarContent}
      </div>
      {sidebarOpen && (
        <div className="lg:hidden fixed inset-0 z-20">
          <div className="fixed inset-0 bg-black/50 backdrop-blur-sm" onClick={onToggleSidebar} />
          {sidebarContent}
        </div>
      )}
    </>
  )
}
