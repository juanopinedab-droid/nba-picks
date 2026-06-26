interface WorkspaceSwitcherProps {
  workspace: 'nba' | 'polymarket'
  onSwitch: (ws: 'nba' | 'polymarket') => void
}

export function WorkspaceSwitcher({ workspace, onSwitch }: WorkspaceSwitcherProps) {
  const isPolymarket = workspace === 'polymarket'

  return (
    <button
      onClick={() => onSwitch(isPolymarket ? 'nba' : 'polymarket')}
      className={[
        'ws-btn relative w-full rounded-full text-[11px] font-medium z-10',
        'px-3 py-2.5 border-none outline-none cursor-pointer select-none',
        'overflow-hidden transition-all duration-200',
        'hover:shadow-lg active:shadow-sm',
        isPolymarket
          ? 'bg-gradient-to-r from-amber-500 to-orange-500 animate-gradient-x bg-[length:200%_100%] text-white shadow-sm shadow-amber-500/25'
          : 'bg-gradient-to-r from-purple-600 to-violet-600 animate-gradient-x bg-[length:200%_100%] text-white shadow-sm shadow-purple-500/25',
      ].join(' ')}
    >
      <style>{`
        .ws-btn::before {
          content: '';
          position: absolute;
          inset: -2px;
          background: none;
          border: 2px solid transparent;
          border-radius: 9999px;
          z-index: 0;
          pointer-events: none;
          transition: border-color 0.25s ease;
        }
        .ws-btn:hover::before {
          border-color: currentColor;
        }
        .ws-btn::after {
          content: '';
          position: absolute;
          inset: 0;
          border-radius: 9999px;
          z-index: 0;
          pointer-events: none;
          background: currentColor;
          opacity: 0.15;
          transform: translate(-110%, -110%);
          transform-origin: left top;
          transition: transform 0.35s cubic-bezier(0.4, 0, 0.2, 1);
        }
        .ws-btn:hover::after {
          transform: translate(0, 0);
        }
        .ws-btn:active::after {
          transform: translate(0, 0);
        }
      `}</style>
      <span className="relative z-10">{isPolymarket ? 'NBA / EPL' : 'Polymarket'}</span>
    </button>
  )
}
