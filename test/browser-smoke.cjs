/* Playwright browser smoke test for the React Web UI.
 * Run: NODE_PATH=$(npm root -g) node test/browser-smoke.cjs
 *
 * 两层断言:
 *  1) 导航层:登录、每个 tab、搜索、详情;捕获 console/pageerror/requestfailed(须 0)。
 *  2) 契约层:用 page.on('response') 捕获 /api/* 实际请求,断言发送、SSE 实时推送、
 *     删除、强制拉取的真实 HTTP 往返(2xx),以及 401 错误态不崩页面。
 * 步骤用 step() 包裹,单步失败只记 error 不中断,最终汇总。
 */
const { chromium } = require('playwright')

const BASE = 'http://127.0.0.1:8025'
const MOCK = 'http://127.0.0.1:8080'         // mock 注信控制台(注入短信用)
const SHOTS = 'test/browser-shots'

;(async () => {
  const errors = []
  const apiCalls = []                          // {method, path, status}
  const browser = await chromium.launch({ headless: true })
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 } })
  const page = await ctx.newPage()

  page.on('console', (m) => {
    if (m.type() !== 'error') return
    const t = m.text()
    // 浏览器对 HTTP 失败(401/网络断)自动打 "Failed to load resource",属预期响应非 JS 错误,跳过
    if (/Failed to load resource/.test(t)) return
    errors.push('console: ' + t)
  })
  page.on('pageerror', (e) => errors.push('pageerror: ' + e.message))
  page.on('requestfailed', (r) => {
    const u = r.url()
    if (!u.includes('/api/events')) errors.push('requestfailed: ' + u + ' ' + (r.failure()?.errorText || ''))
  })
  page.on('response', (res) => {
    const u = res.url()
    if (u.includes('/api/') || u.includes('/hook/')) {
      apiCalls.push({ method: res.request().method(), path: u.replace(BASE, '').split('?')[0], status: res.status() })
    }
  })

  const step = async (name, fn) => {
    try { await fn(); console.log('  ✓', name) }
    catch (e) { console.log('  ✗', name, '->', e.message.split('\n')[0]); errors.push('step(' + name + '): ' + e.message.split('\n')[0]) }
  }
  // 断言某 (method, path 子串) 曾以 2xx 发生(在 since 之后的 apiCalls 里)
  const saw2xx = (method, pathSub, since) => {
    const tail = since == null ? apiCalls : apiCalls.slice(since)
    return tail.some((c) => c.method === method && c.path.includes(pathSub) && c.status >= 200 && c.status < 300)
  }
  const clickNav = async (label) => {
    // 先 Esc 关掉可能打开的 Dialog/Sheet/AlertDialog(MessageDetail 等),否则覆盖底部 nav 导致点击超时
    await page.keyboard.press('Escape')
    await page.waitForTimeout(250)
    await page.click(`nav button:has-text("${label}")`)
    await page.waitForTimeout(800)
  }

  // ── 导航层 ──
  await step('load login page', async () => {
    await page.goto(BASE + '/', { waitUntil: 'domcontentloaded', timeout: 15000 })
    await page.waitForSelector('#password', { timeout: 10000 })
  })
  await page.screenshot({ path: SHOTS + '/00-login.png' })

  await step('login', async () => {
    await page.fill('#user', 'admin')
    await page.fill('#password', 'demo-pass')
    await page.click('button:has-text("登录")')
    await page.waitForSelector('nav', { timeout: 10000 })
  })
  await page.waitForTimeout(1500) // let inbox + SSE settle
  await page.screenshot({ path: SHOTS + '/01-inbox.png' })

  for (const [tab, label] of [['send', '发送'], ['status', '状态'], ['settings', '设置']]) {
    await step('tab ' + tab, async () => {
      await clickNav(label)
      await page.screenshot({ path: `${SHOTS}/02-${tab}.png` })
    })
  }

  await step('back to inbox + search', async () => {
    await clickNav('收件')
    await page.fill('input[placeholder*="搜索"]', '验证')
    await page.waitForTimeout(1000)
    await page.screenshot({ path: SHOTS + '/03-search.png' })
    await page.fill('input[placeholder*="搜索"]', '')
    await page.waitForTimeout(700)
  })

  await step('open first message detail', async () => {
    const item = page.locator('main div[role="button"]').first()
    if ((await item.count()) > 0) { await item.click(); await page.waitForTimeout(800); await page.screenshot({ path: SHOTS + '/04-detail.png' }) }
    else throw new Error('no message items found')
  })

  // ── 契约层 ──

  await step('status: POST /api/status/refresh (强制拉取)', async () => {
    await clickNav('状态')
    await page.waitForSelector('button:has-text("强制拉取")', { timeout: 5000 })
    const since = apiCalls.length
    await page.click('button:has-text("强制拉取")')
    await page.waitForTimeout(1500)
    if (!saw2xx('POST', '/api/status/refresh', since)) throw new Error('未捕获 POST /api/status/refresh 2xx')
    await page.screenshot({ path: SHOTS + '/05-refresh.png' })
  })

  await step('send: POST /api/send (含中文正文)', async () => {
    await clickNav('发送')
    await page.waitForSelector('#to', { timeout: 5000 })
    await page.fill('#to', '13800138001')
    await page.fill('#text', 'smoke 契约测试 你好')   // 含非 ASCII,顺带验证编码链路
    // 提交 → 弹确认对话框 → 点确认
    const submit = page.locator('button[type=submit]')
    if (await submit.isDisabled()) throw new Error('发送按钮禁用(卡/设备可能离线)')
    const since = apiCalls.length
    await submit.click()
    await page.waitForSelector('[role=alertdialog]:has-text("确认发送")', { timeout: 4000 })
    await page.click('[role=alertdialog] button:has-text("确认发送")')
    await page.waitForTimeout(1500)
    if (!saw2xx('POST', '/api/send', since)) throw new Error('未捕获 POST /api/send 2xx')
    await page.screenshot({ path: SHOTS + '/06-send.png' })
  })

  const probe = '998877'   // 注入短信的唯一标记(验证码),供 SSE/删除步骤定位
  await step('SSE: mock 注入 → 收件箱实时出现新消息', async () => {
    await clickNav('收件')
    await page.waitForTimeout(500)
    // 经 mock 控制台注入一条;mock 会即时 webhook → Hub poll → SSE new_messages → 前端失效刷新
    const res = await fetch(MOCK + '/inject', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sender: '13900SmokeProbe', text: 'SSE 实时推送验证码 ' + probe, code: probe }),
    })
    if (!res.ok) throw new Error('mock /inject 失败: ' + res.status())
    // 等待前端因 SSE 刷新出带验证码徽标的新行(最长 15s,覆盖一轮 poll 兜底)
    await page.waitForSelector(`text=${probe}`, { timeout: 15000 })
    await page.screenshot({ path: SHOTS + '/07-sse-push.png' })
  })

  await step('delete: DELETE /api/messages/{id} (删刚注入的)', async () => {
    // 定位刚注入的那一行(含验证码 probe),点其删除按钮 → 确认
    const row = page.locator('main div[role="button"]:has-text("' + probe + '")').first()
    if ((await row.count()) === 0) throw new Error('未找到注入的消息行')
    const since = apiCalls.length
    await row.locator('button[aria-label="删除"]').click()
    await page.waitForSelector('[role=alertdialog]:has-text("删除这条短信")', { timeout: 4000 })
    await page.click('[role=alertdialog] button:has-text("确认删除")')
    await page.waitForTimeout(1500)
    if (!saw2xx('DELETE', '/api/messages', since)) throw new Error('未捕获 DELETE /api/messages 2xx')
    if ((await page.locator('main div[role="button"]:has-text("' + probe + '")').count()) > 0)
      throw new Error('删除后消息行仍在')
    await page.screenshot({ path: SHOTS + '/08-delete.png' })
  })

  await step('auth: 401 失效不崩页面', async () => {
    // 清掉登录 token,直接请求受保护端点 → 后端应 401;页面不得抛 JS 错误
    const before = errors.length
    await page.evaluate(() => localStorage.removeItem('smshub.token'))
    const since = apiCalls.length
    await page.evaluate(async () => {
      try { await fetch('/api/devices') } catch {}   // 无 Authorization 头 → 后端拒
    })
    await page.waitForTimeout(600)
    const saw401 = apiCalls.slice(since).some((c) => c.status === 401)
    if (!saw401) throw new Error('清 token 后未捕获 401 响应')
    if (errors.length > before) throw new Error('401 期间产生非预期错误: ' + errors.slice(before).join('; '))
    await page.screenshot({ path: SHOTS + '/09-401.png' })
  })

  await browser.close()

  console.log('\n=== Console/page errors:', errors.length, '===')
  errors.slice(0, 40).forEach((e) => console.log('  -', e))
  console.log('\n=== API calls captured:', apiCalls.length, '(sample last 12) ===')
  apiCalls.slice(-12).forEach((c) => console.log('  ', c.method, c.status, c.path))
  console.log('\nDONE, screenshots in', SHOTS)
  if (errors.length) process.exitCode = 1
})().catch((e) => { console.error('SCRIPT FAILED:', e); process.exit(1) })
