import { test, expect } from '@playwright/test'
import { spawn, type ChildProcess } from 'node:child_process'
import path from 'node:path'

const port = Number(process.env.PAPERPILOT_PHASE5A_API_PORT || 8184)
const python = path.resolve('..', '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')

test('PostgreSQL A and real D policy: allowed without execution and explicit rejection', async ({ page }) => {
  let server: ChildProcess | undefined
  try {
    server = spawn(python, ['-m', 'uvicorn', 'app.dev_composition:create_app', '--factory', '--app-dir', 'backend', '--host', '127.0.0.1', '--port', String(port), '--no-access-log'], {
      cwd: '..', windowsHide: true, stdio: 'ignore', env: { ...process.env, PAPERPILOT_C_MODE: 'mock', PAPERPILOT_D_MODE: 'real' },
    })
    for (let i = 0; i < 100; i++) {
      if (server.exitCode !== null) throw new Error('Policy backend exited before readiness')
      try { if ((await fetch(`http://127.0.0.1:${port}/api/v1/health`)).ok) break } catch { /* waiting for own server */ }
      if (i === 99) throw new Error('Policy backend did not become ready')
      await new Promise(resolve => setTimeout(resolve, 100))
    }
    await page.goto('/')
    await page.getByRole('button', { name: '进入演示' }).click()
    await page.getByLabel('新项目名称').fill(`Phase 5D policy ${Date.now()}`)
    await page.getByRole('button', { name: '创建项目' }).click()
    await page.getByRole('link', { name: '查询与审计', exact: true }).click()
    await expect(page.getByText('已注册安全适配器；注册和接口返回不代表 SQL 安全防护已通过验收。')).toBeVisible()
    await page.getByRole('button', { name: '提交查询', exact: true }).click()
    await expect(page.getByText('尚未确认执行，策略通过不等于执行成功')).toBeVisible()
    await expect(page.getByText('allowed / not_executed', { exact: true })).toBeVisible()
    await expect(page.locator('.query-result-table')).toHaveCount(0)
    await page.getByLabel('统计问题').fill('unsupported policy query')
    await page.getByRole('button', { name: '提交查询', exact: true }).click()
    await expect(page.getByText('查询被策略拒绝，未执行')).toBeVisible()
    await expect(page.getByText('rejected / not_executed', { exact: true })).toBeVisible()
    await expect(page.locator('.query-result-table')).toHaveCount(0)
  } finally {
    if (server && server.exitCode === null) {
      const ended = new Promise<void>(resolve => server!.once('exit', () => resolve()))
      server.kill()
      await ended
    }
  }
})
