import { useEffect, useRef } from 'react'
import { marked } from 'marked'
import { markedHighlight } from 'marked-highlight'
import hljs from 'highlight.js'
import 'highlight.js/styles/atom-one-dark.css'

import copyIconUrl from './icons/copy.svg'
import checkIconUrl from './icons/check.svg'
import warningIcon from './icons/warning.svg?raw'
import infoIcon from './icons/info.svg?raw'
import tipIcon from './icons/tip.svg?raw'
import cautionIcon from './icons/caution.svg?raw'
import noteIcon from './icons/note.svg?raw'

import './MarkdownRenderer.css'

marked.use(markedHighlight({
  langPrefix: 'hljs language-',
  highlight(code, lang) {
    if (lang === 'mermaid') return code
    const language = hljs.getLanguage(lang) ? lang : 'plaintext'
    return hljs.highlight(code, { language }).value
  }
}))

interface MarkdownRendererProps {
  content: string
}

function transformCustomTags(html: string): string {
  html = html.replace(
    /<Callout\s+type=["'](\w+)["']\s*>([\s\S]*?)<\/Callout>/gi,
    (_match, type: string, inner: string) => {
      const normalizedType = type.toLowerCase()
      const icons: Record<string, string> = {
        warning: warningIcon,
        info: infoIcon,
        tip: tipIcon,
        caution: cautionIcon,
        note: noteIcon
      }
      const icon = icons[normalizedType] || noteIcon
      const label = normalizedType.charAt(0).toUpperCase() + normalizedType.slice(1)
      return `<div class="custom-callout callout-${normalizedType}">
        <span class="callout-icon">${icon}</span>
        <div class="callout-content"><strong>${label}</strong><br>${inner.trim()}</div>
      </div>`
    }
  )

  let tabGroupId = 0
  html = html.replace(
    /<Tabs>([\s\S]*?)<\/Tabs>/gi,
    (_match, inner: string) => {
      const groupId = `tab-group-${tabGroupId++}`
      const tabItems: { label: string; content: string }[] = []
      const tabItemRegex = /<TabItem\s+label=["']([^"']+)["']\s*>([\s\S]*?)<\/TabItem>/gi
      let tabMatch

      while ((tabMatch = tabItemRegex.exec(inner)) !== null) {
        tabItems.push({ label: tabMatch[1], content: tabMatch[2].trim() })
      }

      if (tabItems.length === 0) return inner

      const buttons = tabItems.map((item, i) =>
        `<button class="tab-btn${i === 0 ? ' tab-active' : ''}" data-tab-group="${groupId}" data-tab-index="${i}">${item.label}</button>`
      ).join('')

      const panels = tabItems.map((item, i) =>
        `<div class="tab-panel${i === 0 ? ' tab-panel-active' : ''}" data-tab-group="${groupId}" data-tab-index="${i}">${item.content}</div>`
      ).join('')

      return `<div class="tabs-container" data-tab-group="${groupId}">
        <div class="tabs-header">${buttons}</div>
        <div class="tabs-body">${panels}</div>
      </div>`
    }
  )

  return html
}

export function MarkdownRenderer({ content }: MarkdownRendererProps) {
  const containerRef = useRef<HTMLDivElement>(null)

  const html = (() => {
    let raw = marked.parse(content, { async: false }) as string

    raw = raw.replace(/<table>/g, '<div class="table-wrapper"><table>')
    raw = raw.replace(/<\/table>/g, '</table></div>')

    raw = raw.replace(
      /<pre><code class="hljs language-mermaid">([\s\S]*?)<\/code><\/pre>/g,
      '<pre class="mermaid">$1</pre>'
    )

    raw = raw.replace(
      /<pre><code/g,
      `<pre><button class="code-copy-btn" title="Copy"><img src="${copyIconUrl}" alt="copy"></button><code`
    )

    raw = transformCustomTags(raw)
    return raw
  })()

  useEffect(() => {
    const container = containerRef.current
    if (!container) return

    // Copy buttons
    container.querySelectorAll('.code-copy-btn').forEach(btn => {
      const handler = async () => {
        const pre = btn.closest('pre')
        if (!pre) return
        const code = pre.querySelector('code')
        if (!code) return
        await navigator.clipboard.writeText(code.textContent || '')
        const img = btn.querySelector('img') as HTMLImageElement
        if (img) {
          img.src = checkIconUrl
          setTimeout(() => { img.src = copyIconUrl }, 1500)
        }
      }
      btn.addEventListener('click', handler)
    })

    // Tab switching
    container.querySelectorAll('.tab-btn').forEach(btn => {
      const handler = () => {
        const group = (btn as HTMLElement).dataset.tabGroup
        const index = (btn as HTMLElement).dataset.tabIndex
        if (!group || index === undefined) return
        container.querySelectorAll(`.tab-btn[data-tab-group="${group}"]`).forEach(b => b.classList.remove('tab-active'))
        container.querySelectorAll(`.tab-panel[data-tab-group="${group}"]`).forEach(p => p.classList.remove('tab-panel-active'))
        btn.classList.add('tab-active')
        container.querySelector(`.tab-panel[data-tab-group="${group}"][data-tab-index="${index}"]`)?.classList.add('tab-panel-active')
      }
      btn.addEventListener('click', handler)
    })

    // Mermaid
    const mermaidBlocks = container.querySelectorAll('pre.mermaid')
    if (mermaidBlocks.length > 0) {
      import('mermaid').then(({ default: mermaid }) => {
        mermaid.initialize({
          startOnLoad: false,
          theme: 'dark',
          fontFamily: 'Inter, sans-serif',
        })
        mermaid.run({ nodes: mermaidBlocks as NodeListOf<HTMLElement> })
      })
    }
  }, [html])

  return (
    <div
      ref={containerRef}
      className="md-render"
      dangerouslySetInnerHTML={{ __html: html }}
    />
  )
}
