import { test, expect } from '@playwright/test'
import { spawn, type ChildProcess } from 'node:child_process'
import path from 'node:path'

test('Phase 6.5 PostgreSQL credentials, refresh and logout', async ({ page, context }) => {
  const port = Number(process.env.PAPERPILOT_PHASE6_API_PORT)
  const password = process.env.PAPERPILOT_PHASE65_TEST_PASSWORD
  if (!password) throw new Error('Isolated credential fixture not configured')
  const python = process.env.PAPERPILOT_E2E_PYTHON || path.resolve('..', '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
  let server: ChildProcess | undefined = spawn(python, ['-m', 'app.selector_server', '--port', String(port)], {
    cwd: '..', windowsHide: true, stdio: 'ignore', env: process.env,
  })
  try {
    let ready = false
    for (let i = 0; i < 100; i++) {
      if (server.exitCode !== null) throw new Error('Authentication server exited before readiness')
      try { if ((await fetch(`http://127.0.0.1:${port}/api/v1/health`)).ok) { ready = true; break } } catch { /* wait */ }
      await new Promise(resolve => setTimeout(resolve, 100))
    }
    if (!ready) throw new Error('Authentication server did not become ready')
    await page.goto('/')
    await expect(page.getByRole('heading', { name: '登录', exact: true })).toBeVisible()
    await expect(page.getByLabel('演示身份')).toHaveCount(0)
    await page.getByLabel('账号').fill('alice')
    await page.getByLabel('密码').fill('invalid-isolated-credential')
    await page.getByLabel('密码').press('Enter')
    await expect(page.getByText('账号或密码不正确', { exact: false })).toBeVisible()
    await page.getByLabel('密码').fill(password)
    await page.getByLabel('密码').press('Enter')
    await expect(page.getByRole('heading', { name: '你的研究项目' })).toBeVisible()
    const session = (await context.cookies()).find(cookie => cookie.name === 'pp_session')
    expect(session?.httpOnly).toBe(true)
    expect(await page.evaluate(() => document.cookie.includes('pp_session='))).toBe(false)
    await page.reload()
    await expect(page.getByRole('heading', { name: '你的研究项目' })).toBeVisible()
    await page.getByRole('button', { name: '退出' }).click()
    await expect(page.getByRole('heading', { name: '登录', exact: true })).toBeVisible()
    const replay = await page.request.get('/api/v1/auth/me', { headers: { Cookie: `pp_session=${session!.value}` } })
    expect(replay.status()).toBe(401)
  } finally {
    if (server && server.exitCode === null) {
      const ended = new Promise<void>(resolve => server!.once('exit', () => resolve()))
      server.kill()
      await ended
    }
    server = undefined
  }
})
