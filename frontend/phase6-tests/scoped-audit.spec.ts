import { test, expect } from '@playwright/test'
import { spawn, type ChildProcess } from 'node:child_process'
import path from 'node:path'
import { writeFile, access } from 'node:fs/promises'

test('Phase 6.7 credential session, restricted SQL and scoped audit', async ({ page, context }) => {
  const port = Number(process.env.PAPERPILOT_PHASE6_API_PORT)
  const project = (process.env.PAPERPILOT_PHASE67_FIRST_PROJECT_ID || process.env.PAPERPILOT_SECURITY_DB_PROJECT_ID)!
  const secondProject = process.env.PAPERPILOT_PHASE67_SECOND_PROJECT_ID!
  const password = process.env.PAPERPILOT_PHASE67_TEST_PASSWORD!
  const faultDir = process.env.PAPERPILOT_PHASE67_FAULT_DIR!
  const python = process.env.PAPERPILOT_E2E_PYTHON || path.resolve('..', '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
  let server: ChildProcess | undefined
  async function start() {
    server = spawn(python, ['-m', 'app.selector_server', '--port', String(port)], {
      cwd: '..', windowsHide: true, stdio: 'ignore', env: process.env,
    })
    let lastStatus = "unavailable"
    for (let i = 0; i < 100; i++) {
      if (server.exitCode !== null) throw new Error('Isolated server exited before readiness')
      try {
        const health = await fetch(`http://127.0.0.1:${port}/api/v1/health`)
        if (health.ok) return
        lastStatus = `${health.status}:${(await health.json()).code || 'UNKNOWN'}`
      } catch { /* wait */ }
      await new Promise(resolve => setTimeout(resolve, 100))
    }
    throw new Error(`Isolated server did not become ready (${lastStatus})`)
  }
  async function stop() {
    if (server && server.exitCode === null) {
      const ended = new Promise<void>(resolve => server!.once('exit', () => resolve()))
      server.kill(); await ended
    }
    server = undefined
  }
  try {
    await start()
    await page.goto('/')
    await page.getByLabel('账号').fill('alice')
    await page.getByLabel('密码').fill(password)
    await page.getByRole('button', { name: '登录', exact: true }).click()
    await expect(page.getByRole('heading', { name: '你的研究项目' })).toBeVisible()
    await page.goto(`/projects/${project}/security`)
    await expect(page.getByText('凭据认证', { exact: true })).toBeVisible()
    const executedResponse = page.waitForResponse(r => r.url().endsWith('/secure-queries') && r.request().method() === 'POST')
    await page.getByRole('button', { name: '提交查询', exact: true }).click()
    const executed = await (await executedResponse).json()
    expect(executed).toMatchObject({ decision: 'allowed', execution_status: 'executed', mock: false, rows: [{ year: 2020, count: 1 }] })
    await expect(page.getByText('allowed / executed', { exact: true })).toBeVisible()
    await expect(page.locator('.audit-table')).toContainText(executed.request_id)
    await page.getByLabel('统计问题').fill('unsupported policy query')
    const rejectionResponse = page.waitForResponse(r => r.url().endsWith('/secure-queries') && r.request().method() === 'POST')
    await page.getByRole('button', { name: '提交查询', exact: true }).click()
    expect(await (await rejectionResponse).json()).toMatchObject({ decision: 'rejected', execution_status: 'not_executed' })
    await stop(); await start(); await page.reload()
    await expect(page.locator('.audit-table')).toContainText(executed.request_id)
    const csrf = (await context.cookies()).find(cookie => cookie.name === 'pp_csrf')!.value
    const headers = { Origin: new URL(page.url()).origin, 'X-CSRF-Token': csrf }
    const member = await page.request.post(`/api/v1/projects/${project}/members`, { headers, data: { username: 'bob' } })
    expect(member.status()).toBe(201)
    const bobId = (await member.json()).user.user_id
    expect((await page.request.post('/api/v1/auth/logout', { headers })).status()).toBe(200)
    expect((await page.request.post('/api/v1/auth/login', { headers, data: { username: 'bob', password } })).status()).toBe(200)
    const secondAudit = await page.request.get(`/api/v1/projects/${secondProject}/audit-events`, { params: { action: 'scope.fixture' } })
    expect(secondAudit.status()).toBe(200)
    const secondItems = (await secondAudit.json()).items
    expect(secondItems).toHaveLength(1)
    expect(secondItems[0].project_id).toBe(secondProject)
    await page.goto(`/projects/${secondProject}/security`)
    const secondQueryResponse = page.waitForResponse(r => r.url().endsWith('/secure-queries') && r.request().method() === 'POST')
    await page.getByRole('button', { name: '提交查询', exact: true }).click()
    expect(await (await secondQueryResponse).json()).toMatchObject({ decision: 'allowed', execution_status: 'executed', mock: false, rows: [] })
    await expect(page.getByText('allowed / executed', { exact: true })).toBeVisible()
    expect((await page.request.get(`/api/v1/projects/${project}/audit-events`)).status()).toBe(403)
    expect((await page.request.post(`/api/v1/projects/${project}/secure-queries`, { headers, data: { question: '按年份统计论文数量' } })).status()).toBe(200)
    expect((await page.request.post('/api/v1/auth/logout', { headers })).status()).toBe(200)
    expect((await page.request.post('/api/v1/auth/login', { headers, data: { username: 'alice', password } })).status()).toBe(200)
    expect((await page.request.delete(`/api/v1/projects/${project}/members/${bobId}`, { headers })).status()).toBe(200)
    expect((await page.request.post('/api/v1/auth/logout', { headers })).status()).toBe(200)
    expect((await page.request.post('/api/v1/auth/login', { headers, data: { username: 'bob', password } })).status()).toBe(200)
    expect((await page.request.get(`/api/v1/projects/${project}/audit-events`)).status()).toBe(404)
    expect((await page.request.post(`/api/v1/projects/${project}/secure-queries`, { headers, data: { question: '按年份统计论文数量' } })).status()).toBe(404)
    expect((await page.request.post('/api/v1/auth/logout', { headers })).status()).toBe(200)
    expect((await page.request.post('/api/v1/auth/login', { headers, data: { username: 'alice', password } })).status()).toBe(200)
    await page.goto(`/projects/${project}/security`)
    await page.getByRole('button', { name: '查看运行说明' }).click()
    const modeDialog = page.getByRole('dialog', { name: '当前运行说明' })
    await expect(modeDialog).toContainText('适配器开发 · 待验收')
    await modeDialog.getByRole('button', { name: 'Close this dialog' }).click()
    await writeFile(path.join(faultDir, 'fault-request'), 'ready')
    let injected = false
    for (let i = 0; i < 200; i++) {
      try { await access(path.join(faultDir, 'fault-ready')); injected = true; break } catch { /* wait */ }
      await new Promise(resolve => setTimeout(resolve, 50))
    }
    expect(injected).toBe(true)
    await page.getByLabel('统计问题').fill('按年份统计论文数量')
    const failedResponse = page.waitForResponse(r => r.url().endsWith('/secure-queries') && r.request().method() === 'POST')
    await page.getByRole('button', { name: '提交查询', exact: true }).click()
    const failed = await failedResponse
    expect(failed.status()).toBe(503)
    const failedBody = await failed.json()
    expect(failedBody).toMatchObject({ code: 'AUDIT_UNAVAILABLE' })
    await expect(page.getByRole('alert').filter({ hasText: failedBody.request_id })).toContainText('审计服务暂不可用')
    expect((await page.request.post('/api/v1/auth/logout', { headers })).status()).toBe(200)
    expect((await page.request.get(`/api/v1/projects/${project}/audit-events`)).status()).toBe(401)
    expect((await page.request.post('/api/v1/auth/login', { headers, data: { username: 'bob', password } })).status()).toBe(200)
    expect((await page.request.get(`/api/v1/projects/${project}/audit-events`)).status()).toBe(404)
    expect((await page.request.post(`/api/v1/projects/${project}/secure-queries`, { headers, data: { question: '按年份统计论文数量' } })).status()).toBe(404)
  } finally { await stop() }
})
