import { useState, useRef, useEffect } from 'react'
import { Landmark, TrendingUp, TrendingDown, Wallet, ChevronUp, ChevronDown, Loader2, Key, Menu, MoreHorizontal } from 'lucide-react'
import { api } from '@/lib/api'
import { ApiKeysModal } from '@/components/ApiKeysModal'

interface HeaderProps {
  title: string
  balance: number
  wins: number
  losses: number
  onRefresh?: () => void
  onToggleSidebar?: () => void
}

const TITLES: Record<string, string> = {
  scanner: 'Scanner',
  portfolio: 'Portfolio',
  history: 'History',
  laboratory: 'Laboratory',
  'ai-research': 'AI Research',
}

export function PolymarketHeader({ title, balance, wins, losses, onRefresh, onToggleSidebar }: HeaderProps) {
  const [open, setOpen] = useState(false)
  const [depositAmount, setDepositAmount] = useState('')
  const [withdrawAmount, setWithdrawAmount] = useState('')
  const [actionLoading, setActionLoading] = useState(false)
  const [showKeys, setShowKeys] = useState(false)
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false)
  const popRef = useRef<HTMLDivElement>(null)
  const mobilePopRef = useRef<HTMLDivElement>(null)
  const depositRef = useRef<HTMLInputElement>(null)
  const withdrawRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (popRef.current && !popRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
      if (mobilePopRef.current && !mobilePopRef.current.contains(e.target as Node)) {
        setMobileMenuOpen(false)
      }
    }
    if (open || mobileMenuOpen) document.addEventListener('mousedown', handleClick)
    return () => document.removeEventListener('mousedown', handleClick)
  }, [open, mobileMenuOpen])

  const spin = (ref: React.RefObject<HTMLInputElement | null>, up: boolean) => {
    const el = ref.current
    if (!el) return
    const step = parseFloat(el.step) || 100
    const min = parseFloat(el.min as string) || 0
    const current = parseFloat(el.value) || 0
    const next = up ? current + step : current - step
    if (next >= min) {
      el.value = String(next)
      el.dispatchEvent(new Event('input', { bubbles: true }))
    }
  }

  const doDeposit = async () => {
    const val = parseFloat(depositAmount)
    if (!val || val <= 0) return
    setActionLoading(true)
    try {
      await api.pm.treasury.deposit(val, 'Manual deposit')
      setDepositAmount('')
      onRefresh?.()
    } catch { /* ignore */ }
    setActionLoading(false)
  }

  const doWithdraw = async () => {
    const val = parseFloat(withdrawAmount)
    if (!val || val <= 0) return
    if (val > balance) return
    setActionLoading(true)
    try {
      await api.pm.treasury.withdraw(val, 'Manual withdrawal')
      setWithdrawAmount('')
      onRefresh?.()
    } catch { /* ignore */ }
    setActionLoading(false)
  }

  const total = wins + losses
  const winPct = total > 0 ? ((wins / total) * 100).toFixed(0) + '%' : '--'

  return (
    <header className="flex items-center justify-between px-6 py-3 bg-card border-b border-border">
      <div className="flex items-center gap-3">
        <button onClick={onToggleSidebar} className="lg:hidden text-muted hover:text-foreground">
          <Menu className="w-5 h-5" />
        </button>
        <h1 className="text-lg font-semibold text-foreground">
        {TITLES[title] || title}
      </h1>
      </div>

      <div className="flex items-center gap-4 text-sm">
        <div className="relative" ref={popRef}>
          <button
            onClick={() => setOpen(!open)}
            className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors"
          >
            <Wallet className="w-4 h-4 text-accent" />
            <span className="font-mono font-medium text-foreground">
              ${balance.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} USD
            </span>
          </button>
          {open && (
            <div className="absolute right-0 top-9 w-72 bg-background border border-border rounded-xl shadow-xl p-4 z-50">
              <div className="text-xs text-muted uppercase tracking-wider mb-3">Bankroll</div>

              <div className="flex items-center justify-between mb-4 p-3 bg-slate-50 dark:bg-slate-800/60 rounded-lg">
                <span className="text-sm text-muted">Balance</span>
                <span className="text-lg font-bold text-foreground">
                  ${balance.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} USD
                </span>
              </div>

              <div className="mb-3">
                <label className="text-xs text-muted block mb-1.5">Deposit</label>
                <div className="flex items-center rounded-lg border border-emerald-300 dark:border-emerald-700 focus-within:border-emerald-500 dark:focus-within:border-emerald-600 focus-within:ring-1 focus-within:ring-emerald-400/30 focus-within:bg-gradient-to-r focus-within:from-emerald-500 focus-within:via-emerald-400 focus-within:to-emerald-500 dark:focus-within:from-emerald-700 dark:focus-within:via-emerald-600 dark:focus-within:to-emerald-700 focus-within:bg-[length:200%_100%] focus-within:animate-gradient-x focus-within:[&_input]:text-white focus-within:[&_input]:font-semibold transition-all duration-300 overflow-hidden group">
                  <span className="text-white font-semibold opacity-0 w-0 group-focus-within:opacity-100 group-focus-within:w-auto group-focus-within:ml-2 transition-all duration-300">$</span>
                  <input
                    ref={depositRef}
                    type="number"
                    min={1}
                    step={100}
                    placeholder="1,000"
                    value={depositAmount}
                    onChange={e => setDepositAmount(e.target.value)}
                    onKeyDown={e => e.key === 'Enter' && doDeposit()}
                    disabled={actionLoading}
                    className="spin-emerald flex-1 bg-transparent border-none outline-none py-2 pl-1 pr-0 text-sm text-foreground placeholder:text-muted/40"
                  />
                  <div className="flex flex-col shrink-0 opacity-0 w-0 group-hover:opacity-100 group-hover:w-7 group-focus-within:opacity-100 group-focus-within:w-7 transition-all duration-300">
                    <button type="button" onClick={() => spin(depositRef, true)} disabled={actionLoading} className="flex-1 flex items-center justify-center hover:bg-emerald-600/30">
                      <ChevronUp className="w-3 h-3 opacity-70 group-focus-within:opacity-100" />
                    </button>
                    <button type="button" onClick={() => spin(depositRef, false)} disabled={actionLoading} className="flex-1 flex items-center justify-center hover:bg-emerald-600/30">
                      <ChevronDown className="w-3 h-3 opacity-70 group-focus-within:opacity-100" />
                    </button>
                  </div>
                </div>
                {actionLoading && depositAmount && <Loader2 className="w-3 h-3 animate-spin text-emerald-400 mt-1 ml-1" />}
              </div>

              <div>
                <label className="text-xs text-muted block mb-1.5">Withdraw</label>
                <div className="flex items-center rounded-lg border border-rose-300 dark:border-rose-700 focus-within:border-rose-500 dark:focus-within:border-rose-600 focus-within:ring-1 focus-within:ring-rose-400/30 focus-within:bg-gradient-to-r focus-within:from-rose-500 focus-within:via-rose-400 focus-within:to-rose-500 dark:focus-within:from-rose-700 dark:focus-within:via-rose-600 dark:focus-within:to-rose-700 focus-within:bg-[length:200%_100%] focus-within:animate-gradient-x focus-within:[&_input]:text-white focus-within:[&_input]:font-semibold transition-all duration-300 overflow-hidden group">
                  <span className="text-white font-semibold opacity-0 w-0 group-focus-within:opacity-100 group-focus-within:w-auto group-focus-within:ml-2 transition-all duration-300">$</span>
                  <input
                    ref={withdrawRef}
                    type="number"
                    min={1}
                    step={100}
                    placeholder="500"
                    value={withdrawAmount}
                    onChange={e => setWithdrawAmount(e.target.value)}
                    onKeyDown={e => e.key === 'Enter' && doWithdraw()}
                    disabled={actionLoading}
                    className="spin-rose flex-1 bg-transparent border-none outline-none py-2 pl-1 pr-0 text-sm text-foreground placeholder:text-muted/40"
                  />
                  <div className="flex flex-col shrink-0 opacity-0 w-0 group-hover:opacity-100 group-hover:w-7 group-focus-within:opacity-100 group-focus-within:w-7 transition-all duration-300">
                    <button type="button" onClick={() => spin(withdrawRef, true)} disabled={actionLoading} className="flex-1 flex items-center justify-center hover:bg-rose-600/30">
                      <ChevronUp className="w-3 h-3 opacity-70 group-focus-within:opacity-100" />
                    </button>
                    <button type="button" onClick={() => spin(withdrawRef, false)} disabled={actionLoading} className="flex-1 flex items-center justify-center hover:bg-rose-600/30">
                      <ChevronDown className="w-3 h-3 opacity-70 group-focus-within:opacity-100" />
                    </button>
                  </div>
                </div>
                {actionLoading && withdrawAmount && <Loader2 className="w-3 h-3 animate-spin text-rose-400 mt-1 ml-1" />}
              </div>
            </div>
          )}
        </div>

        <div className="hidden sm:flex items-center gap-3 text-sm">
          <span className="flex items-center gap-1 text-green-600">
            <TrendingUp className="w-4 h-4" />
            <span className="font-mono">{wins}</span>
          </span>
          <span className="flex items-center gap-1 text-red-600">
            <TrendingDown className="w-4 h-4" />
            <span className="font-mono">{losses}</span>
          </span>
          <span className="text-muted font-mono text-xs">({winPct})</span>

          <button
            onClick={() => setShowKeys(true)}
            className="p-1.5 rounded hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors ml-1"
            title="API Keys"
          >
            <Key className="w-4 h-4 text-muted hover:text-foreground" />
          </button>
        </div>

        <div className="relative sm:hidden" ref={mobilePopRef}>
          <button
            onClick={() => setMobileMenuOpen(!mobileMenuOpen)}
            className="p-1.5 rounded hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors"
          >
            <MoreHorizontal className="w-5 h-5 text-muted" />
          </button>
          {mobileMenuOpen && (
            <div className="absolute right-0 top-8 w-48 bg-background border border-border rounded-xl shadow-xl p-3 z-50 space-y-2">
              <div className="flex items-center justify-between text-sm px-1">
                <span className="text-muted text-xs">Record</span>
                <div className="flex items-center gap-3">
                  <span className="flex items-center gap-1 text-green-600 text-xs">
                    <TrendingUp className="w-3.5 h-3.5" />
                    <span className="font-mono">{wins}</span>
                  </span>
                  <span className="flex items-center gap-1 text-red-600 text-xs">
                    <TrendingDown className="w-3.5 h-3.5" />
                    <span className="font-mono">{losses}</span>
                  </span>
                  <span className="text-muted font-mono text-[10px]">({winPct})</span>
                </div>
              </div>
              <button
                onClick={() => { setShowKeys(true); setMobileMenuOpen(false) }}
                className="w-full flex items-center gap-2 px-2 py-1.5 rounded text-sm text-muted hover:text-foreground hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors"
              >
                <Key className="w-3.5 h-3.5" />
                API Keys
              </button>
            </div>
          )}
        </div>
      </div>
      <ApiKeysModal open={showKeys} onClose={() => setShowKeys(false)} />
    </header>
  )
}
