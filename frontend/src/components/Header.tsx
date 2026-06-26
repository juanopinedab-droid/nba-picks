import { useState, useRef, useEffect } from 'react'
import { Wallet, TrendingUp, ChevronUp, ChevronDown, Menu } from 'lucide-react'
import { api } from '@/lib/api'
import { fmt } from '@/lib/utils'

interface HeaderProps {
  title: string
  bankroll?: string
  record?: string
  bankrollValue?: number
  onRefresh?: () => void
  onToggleSidebar?: () => void
}

const labels: Record<string, string> = {
  picks: 'NBA Picks del Dia',
  football: 'Premier League',
  pending: 'Picks Pendientes',
  history: 'Historial de Apuestas',
  backtest: 'Validacion del Modelo',
  calibrate: 'Calibracion',
  bankroll: 'Bankroll',
  help: 'Guia CLI → Frontend',
}

export function Header({ title, bankroll, record, bankrollValue, onRefresh, onToggleSidebar }: HeaderProps) {
  const [open, setOpen] = useState(false)
  const [depositAmount, setDepositAmount] = useState('')
  const [withdrawAmount, setWithdrawAmount] = useState('')
  const popRef = useRef<HTMLDivElement>(null)
  const depositRef = useRef<HTMLInputElement>(null)
  const withdrawRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (popRef.current && !popRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    if (open) document.addEventListener('mousedown', handleClick)
    return () => document.removeEventListener('mousedown', handleClick)
  }, [open])

  const spin = (ref: React.RefObject<HTMLInputElement | null>, up: boolean) => {
    const el = ref.current
    if (!el) return
    const step = parseInt(el.step) || 1000
    const min = parseInt(el.min as string) || 0
    const current = parseInt(el.value) || 0
    const next = up ? current + step : current - step
    if (next >= min) {
      el.value = String(next)
      el.dispatchEvent(new Event('input', { bubbles: true }))
    }
  }

  const doDeposit = async () => {
    const val = parseInt(depositAmount)
    if (val && val > 0) {
      await api.bankroll.deposit(val)
      setDepositAmount('')
      onRefresh?.()
    }
  }

  const doWithdraw = async () => {
    const val = parseInt(withdrawAmount)
    if (val && val > 0) {
      await api.bankroll.withdraw(val)
      setWithdrawAmount('')
      onRefresh?.()
    }
  }

  return (
    <header className="h-14 bg-background border-b border-border flex items-center justify-between px-6">
      <div className="flex items-center gap-3">
        <button onClick={onToggleSidebar} className="lg:hidden text-muted hover:text-foreground">
          <Menu className="w-5 h-5" />
        </button>
        <h1 className="text-lg font-semibold text-foreground">
          {labels[title] || title}
        </h1>
      </div>
      <div className="flex items-center gap-4 text-sm text-muted">
        <div className="relative" ref={popRef}>
          <button
            onClick={() => setOpen(!open)}
            className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors"
          >
            <Wallet className="w-4 h-4" />
            {bankroll}
          </button>
          {open && (
            <div className="absolute right-0 top-9 w-72 bg-background border border-border rounded-xl shadow-xl p-4 z-50">
              <div className="text-xs text-muted uppercase tracking-wider mb-3">Bankroll</div>

              <div className="flex items-center justify-between mb-4 p-3 bg-slate-50 dark:bg-slate-800/60 rounded-lg">
                <span className="text-sm text-muted">Saldo</span>
                <span className="text-lg font-bold text-foreground">
                  ${bankrollValue ? fmt(bankrollValue) : '0'} COP
                </span>
              </div>

              <div className="mb-3">
                <label className="text-xs text-muted block mb-1.5">Depositar</label>
                <div className="flex items-center rounded-lg border border-emerald-300 dark:border-emerald-700 focus-within:border-emerald-500 dark:focus-within:border-emerald-600 focus-within:ring-1 focus-within:ring-emerald-400/30 focus-within:bg-gradient-to-r focus-within:from-emerald-500 focus-within:via-emerald-400 focus-within:to-emerald-500 dark:focus-within:from-emerald-700 dark:focus-within:via-emerald-600 dark:focus-within:to-emerald-700 focus-within:bg-[length:200%_100%] focus-within:animate-gradient-x focus-within:[&_input]:text-white focus-within:[&_input]:font-semibold transition-all duration-300 overflow-hidden group">
                  <span className="text-white font-semibold opacity-0 w-0 group-focus-within:opacity-100 group-focus-within:w-auto group-focus-within:ml-2 transition-all duration-300">$</span>
                  <input
                    ref={depositRef}
                    type="number"
                    min={1000}
                    step={1000}
                    placeholder="10,000"
                    value={depositAmount}
                    onChange={e => setDepositAmount(e.target.value)}
                    onKeyDown={e => e.key === 'Enter' && doDeposit()}
                    className="spin-emerald flex-1 bg-transparent border-none outline-none py-2 pl-1 pr-0 text-sm text-foreground placeholder:text-muted/40"
                  />
                  <div className="flex flex-col shrink-0 opacity-0 w-0 group-hover:opacity-100 group-hover:w-7 group-focus-within:opacity-100 group-focus-within:w-7 transition-all duration-300">
                    <button
                      type="button"
                      onClick={() => spin(depositRef, true)}
                      className="flex-1 flex items-center justify-center hover:bg-emerald-600/30"
                    >
                      <ChevronUp className="w-3 h-3 opacity-70 group-focus-within:opacity-100" />
                    </button>
                    <button
                      type="button"
                      onClick={() => spin(depositRef, false)}
                      className="flex-1 flex items-center justify-center hover:bg-emerald-600/30"
                    >
                      <ChevronDown className="w-3 h-3 opacity-70 group-focus-within:opacity-100" />
                    </button>
                  </div>
                </div>
              </div>

              <div>
                <label className="text-xs text-muted block mb-1.5">Retirar</label>
                <div className="flex items-center rounded-lg border border-rose-300 dark:border-rose-700 focus-within:border-rose-500 dark:focus-within:border-rose-600 focus-within:ring-1 focus-within:ring-rose-400/30 focus-within:bg-gradient-to-r focus-within:from-rose-500 focus-within:via-rose-400 focus-within:to-rose-500 dark:focus-within:from-rose-700 dark:focus-within:via-rose-600 dark:focus-within:to-rose-700 focus-within:bg-[length:200%_100%] focus-within:animate-gradient-x focus-within:[&_input]:text-white focus-within:[&_input]:font-semibold transition-all duration-300 overflow-hidden group">
                  <span className="text-white font-semibold opacity-0 w-0 group-focus-within:opacity-100 group-focus-within:w-auto group-focus-within:ml-2 transition-all duration-300">$</span>
                  <input
                    ref={withdrawRef}
                    type="number"
                    min={1000}
                    step={1000}
                    placeholder="5,000"
                    value={withdrawAmount}
                    onChange={e => setWithdrawAmount(e.target.value)}
                    onKeyDown={e => e.key === 'Enter' && doWithdraw()}
                    className="spin-rose flex-1 bg-transparent border-none outline-none py-2 pl-1 pr-0 text-sm text-foreground placeholder:text-muted/40"
                  />
                  <div className="flex flex-col shrink-0 opacity-0 w-0 group-hover:opacity-100 group-hover:w-7 group-focus-within:opacity-100 group-focus-within:w-7 transition-all duration-300">
                    <button
                      type="button"
                      onClick={() => spin(withdrawRef, true)}
                      className="flex-1 flex items-center justify-center hover:bg-rose-600/30"
                    >
                      <ChevronUp className="w-3 h-3 opacity-70 group-focus-within:opacity-100" />
                    </button>
                    <button
                      type="button"
                      onClick={() => spin(withdrawRef, false)}
                      className="flex-1 flex items-center justify-center hover:bg-rose-600/30"
                    >
                      <ChevronDown className="w-3 h-3 opacity-70 group-focus-within:opacity-100" />
                    </button>
                  </div>
                </div>
              </div>
            </div>
          )}
        </div>
        <span className="flex items-center gap-1.5">
          <TrendingUp className="w-4 h-4" />
          {record}
        </span>
      </div>
    </header>
  )
}
