// Headless render of each pet state to web/shots/<state>.png (Mac/Playwright route; on Linux use tools/shoot.sh).
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { launchBrowser } from './browser.js'

const toolDir = path.dirname(fileURLToPath(import.meta.url))

async function main() {
  const browser = await launchBrowser({
    args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'],
  })
  const page = await browser.newPage({ viewport: { width: 320, height: 320 }, deviceScaleFactor: 2 })
  page.on('pageerror', error => console.log('PAGE ERROR', error.message))
  page.on('console', message => {
    if (message.type() === 'error') console.log('CONSOLE', message.text())
  })

  const file = `file://${path.resolve(toolDir, '../dist-preview/index.html')}`
  const shots = ['idle', 'idle&white=1', 'listening', 'thinking', 'working', 'reading', 'reading&white=1', 'analysing', 'approving', 'success', 'recording', 'error', 'sleeping', 'idle&props=twin', 'idle&props=matcha', 'idle&look=floral&flower=1']
  for (const spec of shots) {
    const [state, ...rest] = spec.split('&')
    const q = rest.length ? '&' + rest.join('&') : ''
    const name = spec.replace(/[&=]/g, '_')
    await page.goto(`${file}?${q.includes('white=1') ? '' : 'bg=1&'}state=${state}${q}`)
    await page.waitForTimeout(state === 'success' ? 350 : 1200)
    await page.screenshot({ path: path.resolve(toolDir, `../shots/${name}.png`) })
    console.log('shot', name)
  }
  await browser.close()
}

main().catch(error => {
  console.error(error)
  process.exitCode = 1
})
