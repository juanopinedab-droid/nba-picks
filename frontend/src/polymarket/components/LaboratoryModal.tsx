import { useState, useEffect } from 'react'
import { Loader2, FlaskConical, Save, Trash2, ChevronDown, ChevronUp, Pencil, X } from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { Card, CardContent } from '@/components/ui/Card'
import { Dialog } from '@/components/ui/Dialog'
import { api } from '@/lib/api'

const DEFAULT_WEIGHTS = {
  weight_momentum: 0.25,
  weight_imbalance: 0.25,
  weight_fundamental: 0.25,
  weight_sentiment: 0.10,
  weight_time_penalty: 0.075,
  weight_spread_penalty: 0.075,
}

function resetForm(setters: {
  setName: (v: string) => void
  setStrategyType: (v: string) => void
  setCustomWeights: (v: typeof DEFAULT_WEIGHTS) => void
  setPromptCustomization: (v: string) => void
  setFixedData: (v: string) => void
  setEditingId: (v: number | null) => void
}) {
  setters.setName('')
  setters.setStrategyType('meta_consensus')
  setters.setCustomWeights({ ...DEFAULT_WEIGHTS })
  setters.setPromptCustomization('')
  setters.setFixedData('')
  setters.setEditingId(null)
}

interface LaboratoryModalProps {
  open: boolean
  onClose: () => void
}

export function LaboratoryModal({ open, onClose }: LaboratoryModalProps) {
  const [strategies, setStrategies] = useState<any[]>([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [expandedId, setExpandedId] = useState<number | null>(null)
  const [editingId, setEditingId] = useState<number | null>(null)

  const [name, setName] = useState('')
  const [strategyType, setStrategyType] = useState('meta_consensus')
  const [customWeights, setCustomWeights] = useState({ ...DEFAULT_WEIGHTS })
  const [promptCustomization, setPromptCustomization] = useState('')
  const [fixedData, setFixedData] = useState('')

  const load = () => {
    api.pm.laboratory.strategies.list().then(d => {
      setStrategies(d.strategies || [])
    }).catch(() => {}).finally(() => setLoading(false))
  }

  useEffect(() => { if (open) load() }, [open])

  const handleEdit = async (id: number) => {
    try {
      const d = await api.pm.laboratory.strategies.get(id)
      const s = d.strategy
      if (!s) return
      const params = typeof s.params_json === 'string' ? JSON.parse(s.params_json) : (s.params_json || {})
      setName(s.name || '')
      setStrategyType(s.strategy_type || 'meta_consensus')
      setCustomWeights({
        weight_momentum: params.weight_momentum ?? DEFAULT_WEIGHTS.weight_momentum,
        weight_imbalance: params.weight_imbalance ?? DEFAULT_WEIGHTS.weight_imbalance,
        weight_fundamental: params.weight_fundamental ?? DEFAULT_WEIGHTS.weight_fundamental,
        weight_sentiment: params.weight_sentiment ?? DEFAULT_WEIGHTS.weight_sentiment,
        weight_time_penalty: params.weight_time_penalty ?? DEFAULT_WEIGHTS.weight_time_penalty,
        weight_spread_penalty: params.weight_spread_penalty ?? DEFAULT_WEIGHTS.weight_spread_penalty,
      })
      setPromptCustomization(params.user_prompt_customization || '')
      setFixedData(params.fixed_data_block || '')
      setEditingId(id)
      setExpandedId(null)
    } catch { /* ignore */ }
  }

  const handleSave = async () => {
    if (!name.trim()) return
    setSaving(true)
    const params = {
      ...customWeights,
      user_prompt_customization: promptCustomization,
      fixed_data_block: fixedData,
    }
    try {
      if (editingId) {
        await api.pm.laboratory.strategies.update(editingId, name, strategyType, params, true)
      } else {
        await api.pm.laboratory.strategies.save(name, strategyType, params, true)
      }
      resetForm({ setName, setStrategyType, setCustomWeights, setPromptCustomization, setFixedData, setEditingId })
      load()
    } catch { /* ignore */ }
    setSaving(false)
  }

  const handleDelete = async (id: number) => {
    try {
      await api.pm.laboratory.strategies.delete(id)
      if (editingId === id) {
        resetForm({ setName, setStrategyType, setCustomWeights, setPromptCustomization, setFixedData, setEditingId })
      }
      load()
    } catch { /* ignore */ }
  }

  return (
    <Dialog open={open} onClose={onClose} title="Strategy Laboratory" className="max-w-2xl">
      <div className="space-y-4">
        <Card>
          <CardContent className="space-y-3">
            <div className="flex items-center gap-2">
              <FlaskConical className="w-4 h-4 text-accent" />
              <h2 className="text-sm font-semibold text-foreground">
                {editingId ? 'Edit Strategy' : 'New Strategy'}
              </h2>
              {editingId && <span className="text-xs text-muted ml-auto">Editing #{editingId}</span>}
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-xs text-muted block mb-1">Name</label>
                <input type="text" value={name} onChange={e => setName(e.target.value)}
                  placeholder="My strategy"
                  className="w-full rounded border border-border bg-background text-foreground text-sm px-2 py-1.5" />
              </div>
              <div>
                <label className="text-xs text-muted block mb-1">Type</label>
                <select value={strategyType} onChange={e => setStrategyType(e.target.value)}
                  className="w-full rounded border border-border bg-background text-foreground text-sm px-2 py-1.5">
                  <option value="meta_consensus">Meta Consensus</option>
                  <option value="market_implied">Market Implied</option>
                  <option value="manual">Manual</option>
                  <option value="external">External</option>
                </select>
              </div>
            </div>

            {strategyType === 'meta_consensus' && (
              <div className="space-y-2 border-t border-border pt-2">
                <div className="text-xs text-muted font-medium">Weights</div>
                {Object.entries(customWeights).map(([key, val]) => (
                  <div key={key} className="flex items-center gap-2">
                    <span className="w-36 text-xs text-muted truncate">{key.replace('weight_', '').replace('_', ' ')}</span>
                    <input type="range" min={0} max={1} step={0.01} value={val}
                      onChange={e => setCustomWeights(prev => ({ ...prev, [key]: parseFloat(e.target.value) }))}
                      className="flex-1 h-1 accent-accent" />
                    <span className="w-10 text-xs text-right font-mono text-muted">{(val * 100).toFixed(0)}%</span>
                  </div>
                ))}
              </div>
            )}

            <div className="flex items-center gap-2">
              <Button onClick={handleSave} disabled={saving || !name.trim()} size="sm">
                {saving ? <Loader2 className="w-4 h-4 mr-1 animate-spin" /> : <Save className="w-4 h-4 mr-1" />}
                {editingId ? 'Update' : 'Save'}
              </Button>
              {editingId && (
                <Button variant="ghost" size="sm"
                  onClick={() => resetForm({ setName, setStrategyType, setCustomWeights, setPromptCustomization, setFixedData, setEditingId })}>
                  <X className="w-3.5 h-3.5 mr-1" />Cancel
                </Button>
              )}
            </div>
          </CardContent>
        </Card>

        <div>
          <h3 className="text-sm font-semibold text-foreground mb-2">Saved Strategies ({strategies.length})</h3>
          {loading ? (
            <div className="flex justify-center py-4"><Loader2 className="w-4 h-4 animate-spin text-muted" /></div>
          ) : strategies.length === 0 ? (
            <p className="text-xs text-muted text-center py-4">No saved strategies</p>
          ) : (
            <div className="max-h-48 overflow-y-auto space-y-1 pr-1">
              {strategies.map((s: any) => {
                const isExpanded = expandedId === s.id
                const params = (() => { try { return typeof s.params_json === 'string' ? JSON.parse(s.params_json) : (s.params_json || {}) } catch { return {} } })()
                const weightKeys = ['weight_momentum', 'weight_imbalance', 'weight_fundamental', 'weight_sentiment', 'weight_time_penalty', 'weight_spread_penalty']
                const hasWeights = weightKeys.some(k => params[k] !== undefined)

                return (
                  <div key={s.id}>
                    <div className={`flex items-center justify-between p-2 rounded text-sm ${editingId === s.id ? 'bg-accent/10 border border-accent/30' : 'bg-muted/20'}`}>
                      <div className="flex items-center gap-2 min-w-0">
                        <button onClick={() => setExpandedId(isExpanded ? null : s.id)} className="text-muted hover:text-foreground shrink-0">
                          {isExpanded ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
                        </button>
                        <span className="text-foreground font-medium truncate">{s.name}</span>
                        <span className="text-xs text-muted shrink-0">{s.strategy_type}</span>
                        {s.is_active ? <span className="text-xs text-green-500 shrink-0">active</span> : null}
                      </div>
                      <div className="flex items-center gap-1 shrink-0">
                        <Button variant="ghost" size="icon" onClick={() => handleEdit(s.id)}
                          className="h-7 w-7 text-muted hover:text-accent"><Pencil className="w-3.5 h-3.5" /></Button>
                        <Button variant="ghost" size="icon" onClick={() => handleDelete(s.id)}
                          className="h-7 w-7 text-muted hover:text-red-500"><Trash2 className="w-3.5 h-3.5" /></Button>
                      </div>
                    </div>

                    {isExpanded && (
                      <div className="ml-6 mt-1 p-2 bg-muted/10 rounded border border-border text-xs space-y-1.5">
                        <div className="grid grid-cols-2 gap-x-4 gap-y-0.5">
                          <span className="text-muted">ID</span><span className="font-mono">{s.id}</span>
                          <span className="text-muted">Type</span><span>{s.strategy_type}</span>
                          <span className="text-muted">Active</span><span>{s.is_active ? 'Yes' : 'No'}</span>
                          <span className="text-muted">Created</span><span>{s.created_at?.replace('T', ' ').slice(0, 19) || '—'}</span>
                          <span className="text-muted">Updated</span><span>{s.updated_at?.replace('T', ' ').slice(0, 19) || '—'}</span>
                        </div>
                        {hasWeights && (
                          <div>
                            <div className="text-muted mb-1">Weights</div>
                            <div className="grid grid-cols-2 gap-x-3 gap-y-0.5">
                              {weightKeys.map(k => params[k] !== undefined ? (
                                <div key={k} className="flex justify-between"><span className="text-muted truncate">{k.replace('weight_', '').replace('_', ' ')}</span><span className="font-mono">{(params[k] * 100).toFixed(0)}%</span></div>
                              ) : null)}
                            </div>
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          )}
        </div>
      </div>
    </Dialog>
  )
}
