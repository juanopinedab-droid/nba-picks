import { useEffect, useRef } from 'react'

const GLITCH_NAMES = [
  '予言ネコ',
  '市場の目',
  '猫の慧眼',
  '謎の予測機',
  '\u30b1\u30f3\u30ad\u30e5\u30a6\u5e02\u5834',
  '値札占い',
  'n̷e̷k̷o̷予測',
  '相場の狐',
  '闇市の猫',
  '先見の明',
  '預言市場',
  '未来視.exe',
  '神託ネコ',
  'b̷o̷t̷の独り言',
  '予測不能',
  '猫又相場',
  '市場の迷宮',
  '千里眼',
  '預測引擎',
  '天気予報',
  '裏取引',
  '価格透視',
  'k̷a̷s̷i̷k̷o̷i̷猫',
  '先物の夢',
  '見えざる手',
]

let idx = 1
document.title = GLITCH_NAMES[0]

function nextName(): string {
  idx = (idx + 1) % GLITCH_NAMES.length
  return GLITCH_NAMES[idx]
}

export function useGlitchTitle() {
  const timer = useRef<ReturnType<typeof setInterval>>()

  useEffect(() => {
    timer.current = setInterval(() => {
      document.title = nextName()
    }, 2000 + Math.random() * 3000) // 2-5 seconds random

    return () => clearInterval(timer.current)
  }, [])
}
