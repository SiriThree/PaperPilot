import { test, expect } from '@playwright/test'
import { spawn, type ChildProcess } from 'node:child_process'
import path from 'node:path'

const port = Number(process.env.PAPERPILOT_PHASE6_API_PORT)
const project = process.env.PAPERPILOT_SECURITY_DB_PROJECT_ID!
const python = path.resolve('..', '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
let server: ChildProcess | undefined

async function start() {
  server = spawn(python, ['-m', 'app.selector_server', '--port', String(port)], {
    cwd: '..', windowsHide: true, stdio: 'ignore', env: process.env,
  })
  for (let i = 0; i < 100; i++) {
    if (server.exitCode !== null) throw new Error('Candidate server exited before readiness')
    try { if ((await fetch(`http://127.0.0.1:${port}/api/v1/health`)).ok) return } catch { /* wait */ }
    await new Promise(resolve => setTimeout(resolve, 100))
  }
  throw new Error('Candidate server did not become ready')
}

async function stop() {
  if (server && server.exitCode === null) {
    const ended = new Promise<void>(resolve => server!.once('exit', () => resolve()))
    server.kill()
    await ended
  }
  server = undefined
}

test('restricted PostgreSQL execution, rejection, interruption and audit recovery', async ({ page }) => {
  await start()
  try {
    await page.goto('/')
    await page.getByRole('button', { name: '进入演示' }).click()
    await expect(page.getByRole('heading', { name: '你的研究项目' })).toBeVisible()
    await page.goto(`/projects/${project}/security`)
    await expect(page.getByTestId('scope-role')).toHaveText('负责人')
    const executedResponse = page.waitForResponse(r => r.url().endsWith('/secure-queries') && r.request().method() === 'POST')
    await page.getByRole('button', { name: '提交查询', exact: true }).click()
    const executed = await (await executedResponse).json()
    expect(executed).toMatchObject({ decision: 'allowed', execution_status: 'executed', mock: false, rows: [{ year: 2020, count: 1 }] })
    await expect(page.getByText('allowed / executed', { exact: true })).toBeVisible()
    await expect(page.locator('.query-result-table')).toContainText('2020')
    await expect(page.locator('.audit-table')).toContainText(executed.request_id)

    await page.getByLabel('统计问题').fill('unsupported policy query')
    const rejectionResponse = page.waitForResponse(r => r.url().endsWith('/secure-queries') && r.request().method() === 'POST')
    await page.getByRole('button', { name: '提交查询', exact: true }).click()
    const rejected = await (await rejectionResponse).json()
    expect(rejected).toMatchObject({ decision: 'rejected', execution_status: 'not_executed', rows: [] })
    await expect(page.getByText('rejected / not_executed', { exact: true })).toBeVisible()
    await expect(page.locator('.audit-table')).toContainText(rejected.request_id)

    await stop()
    await page.getByRole('button', { name: '提交查询', exact: true }).click()
    await expect(page.getByRole('alert').filter({ hasText: '服务暂时无法处理请求' })).toBeVisible()
    await expect(page.getByTestId('scope-role')).toHaveText('待确认')
    await expect(page.locator('.query-result-table')).toHaveCount(0)
    await start()
    await page.reload()
    await page.getByRole('button', { name: '进入演示' }).click()
    await expect(page.getByTestId('scope-role')).toHaveText('负责人')
    await expect(page.locator('.audit-table')).toContainText(executed.request_id)
    await expect(page.locator('.audit-table')).toContainText(rejected.request_id)

    const headers = { 'X-PaperPilot-Client': 'web' }
    const base = `/api/v1/projects/${project}`
    const added = await page.request.post(`${base}/members`, { headers, data: { username: 'bob' } })
    expect(added.ok()).toBeTruthy()
    const bob = (await added.json()).user.user_id
    expect((await page.request.post('/api/v1/auth/login', { headers, data: { username: 'bob' } })).ok()).toBeTruthy()
    const other = process.env.PAPERPILOT_PHASE6_OTHER_PROJECT_ID!
    expect((await page.request.post(`/api/v1/projects/${other}/secure-queries`, { headers, data: { question: '按年份统计论文数量' } })).status()).toBe(404)
    expect((await page.request.post(`${base}/secure-queries`, { headers, data: { question: '按年份统计论文数量' } })).ok()).toBeTruthy()
    expect((await page.request.post('/api/v1/auth/login', { headers, data: { username: 'alice' } })).ok()).toBeTruthy()
    expect((await page.request.delete(`${base}/members/${bob}`, { headers })).ok()).toBeTruthy()
    expect((await page.request.post('/api/v1/auth/login', { headers, data: { username: 'bob' } })).ok()).toBeTruthy()
    expect((await page.request.post(`${base}/secure-queries`, { headers, data: { question: '按年份统计论文数量' } })).status()).toBe(404)
    await page.reload()
    await expect(page.getByText('当前项目不存在或不可访问')).toBeVisible()
  } finally { await stop() }
})

test('Phase 6.3 isolated restricted execution and failure recovery', async ({ page }) => {
  await start()
  try {
    if (process.env.PAPERPILOT_AUDIT_PREFLIGHT === 'required') {
      const health = await (await page.request.get('/api/v1/health')).json()
      expect(health.limitations).toContain('audit_preflight_required')
      expect(health.limitations).toContain('security_executor_ready')
    }
    await page.goto('/')
    await page.getByRole('button', { name: '进入演示' }).click()
    await expect(page.getByRole('heading', { name: '你的研究项目' })).toBeVisible()
    await page.goto(`/projects/${project}/security`)
    await expect(page.getByTestId('scope-role')).toHaveText('负责人')
    const response = page.waitForResponse(r => r.url().endsWith('/secure-queries') && r.request().method() === 'POST')
    await page.getByRole('button', { name: '提交查询', exact: true }).click()
    const executed = await (await response).json()
    expect(executed).toMatchObject({ decision: 'allowed', execution_status: 'executed', mock: false, rows: [{ year: 2020, count: 1 }] })
    await expect(page.getByText('allowed / executed', { exact: true })).toBeVisible()
    await expect(page.locator('.audit-table')).toContainText(executed.request_id)
    await page.getByLabel('统计问题').fill('unsupported policy query')
    const rejectedResponse = page.waitForResponse(r => r.url().endsWith('/secure-queries') && r.request().method() === 'POST')
    await page.getByRole('button', { name: '提交查询', exact: true }).click()
    expect(await (await rejectedResponse).json()).toMatchObject({ decision: 'rejected', execution_status: 'not_executed', rows: [] })
    await expect(page.getByText('rejected / not_executed', { exact: true })).toBeVisible()
    await stop()
    await page.getByRole('button', { name: '提交查询', exact: true }).click()
    await expect(page.getByRole('alert').filter({ hasText: '服务暂时无法处理请求' })).toBeVisible()
    await expect(page.locator('.query-result-table')).toHaveCount(0)
    await start()
    await page.reload()
    await page.getByRole('button', { name: '进入演示' }).click()
    await expect(page.getByTestId('scope-role')).toHaveText('负责人')
    await expect(page.locator('.audit-table')).toContainText(executed.request_id)
    const headers = { 'X-PaperPilot-Client': 'web' }
    expect((await page.request.post('/api/v1/auth/login', { headers, data: { username: 'bob' } })).ok()).toBeTruthy()
    const denied = await page.request.post(`/api/v1/projects/${project}/secure-queries`, { headers, data: { question: '按年份统计论文数量' } })
    expect(denied.status()).toBe(404)
    expect((await denied.json()).code).toBe('RESOURCE_NOT_FOUND')
  } finally { await stop() }
})
