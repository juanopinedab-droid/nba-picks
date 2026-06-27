import { createContext, useContext, useReducer, useEffect, useRef, useCallback, type ReactNode } from 'react'
import { api } from '@/lib/api'

export interface JobState {
  id: string
  type: string
  status: 'running' | 'completed' | 'failed'
  progress: number
  log: string[]
  result: any
  error: string | null
  updatedAt: number
  _pollErrors: number
}

type JobsMap = Record<string, JobState>

type Action =
  | { type: 'ADD_JOB'; id: string; jobType: string }
  | { type: 'UPDATE_JOB'; id: string; patch: Partial<Omit<JobState, 'id'>> }
  | { type: 'REMOVE_JOB'; id: string }
  | { type: 'LOAD_SNAPSHOT'; snapshot: JobsMap }

function jobsReducer(state: JobsMap, action: Action): JobsMap {
  switch (action.type) {
    case 'ADD_JOB':
      if (state[action.id]) return state
      console.log('[JobsContext] ADD_JOB', action.id, action.jobType)
      const now = Date.now()
      return {
        ...state,
        [action.id]: {
          id: action.id,
          type: action.jobType,
          status: 'running',
          progress: 0,
          log: [],
          result: null,
          error: null,
          updatedAt: now,
          _pollErrors: 0,
        },
      }
    case 'UPDATE_JOB': {
      const current = state[action.id]
      if (!current) return state
      console.log('[JobsContext] UPDATE_JOB', action.id, action.patch.status || current.status, `progress=${action.patch.progress ?? current.progress}`)
      return {
        ...state,
        [action.id]: { ...current, ...action.patch, updatedAt: Date.now() },
      }
    }
    case 'REMOVE_JOB': {
      if (!state[action.id]) return state
      const next = { ...state }
      delete next[action.id]
      return next
    }
    case 'LOAD_SNAPSHOT':
      return { ...action.snapshot }
    default:
      return state
  }
}

interface JobsContextValue {
  jobs: JobsMap
  startJob: (id: string, jobType: string) => void
  dismissJob: (id: string) => void
  getJob: (id: string) => JobState | undefined
  getLatestJob: (jobType: string) => JobState | undefined
}

const JobsContext = createContext<JobsContextValue | null>(null)

const POLL_INTERVAL = 1000

const SNAPSHOT_KEY = 'nba-jobs-snapshot'

function loadSnapshot(): JobsMap {
  try {
    const raw = sessionStorage.getItem(SNAPSHOT_KEY)
    if (!raw) { console.log('[JobsContext] loadSnapshot: vacio'); return {} }
    const parsed = JSON.parse(raw)
    if (typeof parsed !== 'object' || !parsed) return {}
    const snapshot: JobsMap = {}
    let downgraded = 0
    let loaded = 0
    for (const [id, j] of Object.entries(parsed) as [string, any][]) {
      if (j && typeof j.id === 'string') {
        const wasRunning = j.status === 'running'
        if (wasRunning) downgraded++
        else loaded++
        snapshot[id] = {
          id: j.id,
          type: j.type || 'unknown',
          status: wasRunning ? 'failed' : (j.status || 'failed'),
          progress: j.progress || 0,
          log: Array.isArray(j.log) ? j.log : [],
          result: j.result || null,
          error: wasRunning ? 'Job interrumpido (pagina recargada)' : (j.error || null),
          updatedAt: j.updatedAt || 0,
          _pollErrors: 0,
        }
      }
    }
    console.log('[JobsContext] loadSnapshot:', loaded, 'completed,', downgraded, 'downgraded from running')
    return snapshot
  } catch {
    return {}
  }
}

function saveSnapshot(jobs: JobsMap) {
  try {
    const running: JobsMap = {}
    for (const [id, j] of Object.entries(jobs)) {
      running[id] = { ...j }
    }
    sessionStorage.setItem(SNAPSHOT_KEY, JSON.stringify(running))
  } catch { /* ignore */ }
}

export function JobProvider({ children }: { children: ReactNode }) {
  const [jobs, dispatch] = useReducer(jobsReducer, {}, loadSnapshot)
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const jobsRef = useRef(jobs)
  jobsRef.current = jobs

  const poll = useCallback(async () => {
    const current = jobsRef.current
    const runningIds = Object.keys(current).filter(
      id => current[id].status === 'running'
    )
    if (runningIds.length === 0) return
    console.log('[JobsContext] POLL checking', runningIds.length, 'running jobs:', runningIds.map(id => `${id.slice(0,8)}(${current[id].type})`).join(', '))

    for (const id of runningIds) {
      try {
        const j = await api.jobs.status(id)
        console.log('[JobsContext] POLL response for', id.slice(0,8), ':', j?.status, j?.error ? `error="${j.error}"` : '')
        if (!j || j.error) {
          dispatch({ type: 'UPDATE_JOB', id, patch: {
            status: 'failed',
            progress: 1,
            error: j?.error || 'Job no encontrado en el servidor',
          }})
          continue
        }
        const patch: Partial<Omit<JobState, 'id'>> = {
          status: j.status || 'running',
          progress: j.progress ?? 0,
          log: Array.isArray(j.log_tail) ? j.log_tail : [],
        }
        if (j.status === 'completed' && j.result) {
          patch.result = j.result
        }
        if (j.error) {
          patch.error = j.error
        }
        dispatch({ type: 'UPDATE_JOB', id, patch })
      } catch {
        const current = jobsRef.current[id]
        const errors = (current?._pollErrors || 0) + 1
        console.log('[JobsContext] POLL error for', id.slice(0,8), `(attempt ${errors}/3)`)
        if (errors >= 3) {
          dispatch({ type: 'UPDATE_JOB', id, patch: {
            status: 'failed',
            progress: 1,
            error: 'Error de conexion al consultar estado del job (3 intentos)',
            _pollErrors: errors,
          }})
        } else {
          dispatch({ type: 'UPDATE_JOB', id, patch: { _pollErrors: errors } })
        }
      }
    }
  }, [])

  useEffect(() => {
    const hasRunning = Object.values(jobs).some(j => j.status === 'running')
    if (hasRunning && !intervalRef.current) {
      intervalRef.current = setInterval(poll, POLL_INTERVAL)
    }
    if (!hasRunning && intervalRef.current) {
      clearInterval(intervalRef.current)
      intervalRef.current = null
    }
  }, [jobs, poll])

  useEffect(() => {
    return () => {
      if (intervalRef.current) {
        clearInterval(intervalRef.current)
      }
    }
  }, [])

  useEffect(() => {
    saveSnapshot(jobs)
  }, [jobs])

  const startJob = useCallback((id: string, jobType: string) => {
    dispatch({ type: 'ADD_JOB', id, jobType })
  }, [])

  const dismissJob = useCallback((id: string) => {
    dispatch({ type: 'REMOVE_JOB', id })
  }, [])

  const getJob = useCallback(
    (id: string) => jobs[id],
    [jobs]
  )

  const getLatestJob = useCallback(
    (jobType: string) => {
      const matches = Object.values(jobs).filter(j => j.type === jobType)
      if (matches.length === 0) return undefined
      const running = matches.find(j => j.status === 'running')
      if (running) return running
      matches.sort((a, b) => (b.updatedAt || 0) - (a.updatedAt || 0))
      return matches[0]
    },
    [jobs]
  )

  return (
    <JobsContext.Provider value={{ jobs, startJob, dismissJob, getJob, getLatestJob }}>
      {children}
    </JobsContext.Provider>
  )
}

export function useJobs() {
  const ctx = useContext(JobsContext)
  if (!ctx) throw new Error('useJobs must be used within JobProvider')
  return ctx
}

export function useJob(jobType: string): {
  job: JobState | undefined
  isRunning: boolean
  log: string[]
  progress: number
  result: any
  picks: any[]
  error: string | null
} {
  const { getLatestJob } = useJobs()
  const job = getLatestJob(jobType)
  return {
    job,
    isRunning: job?.status === 'running',
    log: job?.log || [],
    progress: job?.progress || 0,
    result: job?.result || null,
    picks: job?.result?.picks || [],
    error: job?.error || null,
  }
}
