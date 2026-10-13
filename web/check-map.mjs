// Диагностика: открывает приложение в настоящем Chrome, проходит по
// вкладкам карты, кликает по точке и собирает ошибки консоли и сети.
import { chromium } from 'playwright'

const browser = await chromium.launch({ channel: 'chrome' })
const page = await browser.newPage({ viewport: { width: 1400, height: 900 } })

const logs = []
page.on('pageerror', (e) => logs.push(`[pageerror] ${e.message}`))
page.on('requestfailed', (r) =>
  logs.push(`[failed] ${r.url().slice(0, 100)} — ${r.failure()?.errorText}`)
)
page.on('response', (r) => {
  if (r.status() >= 400) logs.push(`[http ${r.status()}] ${r.url().slice(0, 100)}`)
})

await page.goto('http://localhost:5173/', { waitUntil: 'load' })
await page.waitForFunction(() => document.querySelectorAll('.card').length > 0, { timeout: 60000 })

await page.getByRole('button', { name: 'Карта' }).click()
await page.waitForTimeout(8000)
await page.screenshot({ path: 'map-ru.png' })

// клик по самой крупной точке — в центре России это Москва
const box = await page.locator('.map canvas').boundingBox()
await page.mouse.click(box.x + 147, box.y + 400)
await page.waitForTimeout(1500)
const panel = await page.locator('.map-panel').innerText()
await page.screenshot({ path: 'map-click.png' })

await page.getByRole('button', { name: 'Другие страны' }).click()
await page.waitForTimeout(6000)
await page.screenshot({ path: 'map-foreign.png' })
const foreignHint = await page.locator('.map-panel').innerText()

console.log('панель после клика:', JSON.stringify(panel.slice(0, 120)))
console.log('вкладка «другие страны»:', JSON.stringify(foreignHint.slice(0, 90)))
console.log('--- проблемы (' + logs.length + ') ---')
console.log(logs.slice(0, 20).join('\n') || 'нет')
await browser.close()
