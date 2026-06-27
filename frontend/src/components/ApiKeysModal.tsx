import { useState, useEffect } from 'react'
import { X, Key, Loader2, CheckCircle } from 'lucide-react'
import { api } from '@/lib/api'

interface ApiKeysModalProps {
  open: boolean
  onClose: () => void
}

export function ApiKeysModal({ open, onClose }: ApiKeysModalProps) {
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [deepseekKey, setDeepseekKey] = useState('')
  const [saved, setSaved] = useState(false)
  const [hasKey, setHasKey] = useState(false)

  useEffect(() => {
    if (!open) return
    setLoading(true)
    setSaved(false)
    api.pm.keys.get().then((d: any) => {
      setHasKey(d.configured)
      setDeepseekKey('')
    }).catch(() => {}).finally(() => setLoading(false))
  }, [open])

  const handleSave = async () => {
    if (!deepseekKey.trim()) return
    setSaving(true)
    try {
      await api.pm.keys.save({ DEEPSEEK_API_KEY: deepseekKey.trim() })
      setSaved(true)
      setHasKey(true)
      setDeepseekKey('')
    } catch { /* ignore */ }
    setSaving(false)
  }

  if (!open) return null

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-background/80 backdrop-blur-sm animate-in fade-in duration-200" onClick={onClose}>
      <div className="bg-card border border-border rounded-lg w-full max-w-md mx-4 animate-in zoom-in-95 duration-300" onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between px-5 py-4 border-b border-border">
          <div className="flex items-center gap-2">
            <Key className="w-4 h-4 text-accent" />
            <h3 className="text-sm font-semibold text-foreground">API Keys</h3>
          </div>
          <button onClick={onClose} className="p-1.5 rounded hover:bg-slate-700/20 text-muted hover:text-foreground transition-colors">
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="px-5 py-4 space-y-4">
          {loading ? (
            <div className="flex items-center justify-center py-6">
              <Loader2 className="w-5 h-5 animate-spin text-muted" />
            </div>
          ) : (
            <>
              <div className="space-y-1.5">
                <label className="text-xs text-muted block">
                  DeepSeek API Key
                  {hasKey && <CheckCircle className="w-3 h-3 text-green-400 inline ml-1.5" />}
                </label>
                <input
                  type="password"
                  value={deepseekKey}
                  onChange={e => setDeepseekKey(e.target.value)}
                  placeholder={hasKey ? '•••••••• (stored)' : 'sk-...'}
                  className="w-full rounded border border-border bg-background text-foreground text-sm px-3 py-2 placeholder:text-slate-500/50 focus:border-accent/50 focus:outline-none font-mono"
                  onKeyDown={e => e.key === 'Enter' && handleSave()}
                />
                <p className="text-[10px] text-muted">
                  Stored encrypted on disk. Used for AI research agents.
                </p>
              </div>

              <button
                onClick={handleSave}
                disabled={saving || !deepseekKey.trim()}
                className="w-full py-2 rounded bg-accent text-white font-medium text-sm hover:bg-accent/90 transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2"
              >
                {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : saved ? <CheckCircle className="w-4 h-4" /> : null}
                {saved ? 'Saved' : 'Save'}
              </button>

              {saved && (
                <p className="text-[10px] text-green-400 text-center">
                  Key saved and applied. Active immediately.
                </p>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  )
}
