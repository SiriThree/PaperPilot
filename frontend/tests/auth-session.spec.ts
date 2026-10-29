import { test, expect } from '@playwright/test'

test('credential login verifies the session, recovers on refresh and logs out', async ({ page }) => {
  let signedIn = false
  let loginCalls = 0
  let csrfHeaders = 0
  const identity = { user: { user_id: '00000000-0000-0000-0000-000000000001', username: 'tester', display_name: 'Tester' }, authentication: 'credentials', mock: false }
  await page.route('**/api/v1/**', async route => {
    const path = new URL(route.request().url()).pathname
    if (path.endsWith('/health')) return route.fulfill({ json: { status: 'ok', modes: { a: 'mock', c: 'mock', d: 'mock' }, mock: true, limitations: ['authentication_credentials'] } })
    if (path.endsWith('/csrf')) return route.fulfill({ json: { csrf_token: 'synthetic-csrf-token' } })
    if (path.endsWith('/auth/login')) {
      loginCalls++
      if (route.request().headers()['x-csrf-token'] === 'synthetic-csrf-token') csrfHeaders++
      const body = route.request().postDataJSON()
      if (body.password !== 'synthetic-test-password') return route.fulfill({ status: 401, json: { code: 'INVALID_IDENTITY' } })
      signedIn = true
      return route.fulfill({ json: identity })
    }
    if (path.endsWith('/auth/me')) return signedIn ? route.fulfill({ json: identity }) : route.fulfill({ status: 401, json: { code: 'UNAUTHENTICATED' } })
    if (path.endsWith('/auth/logout')) { signedIn = false; return route.fulfill({ json: { message: 'ok' } }) }
    if (path.endsWith('/projects')) return route.fulfill({ json: { items: [] } })
    return route.fulfill({ status: 404, json: { code: 'RESOURCE_NOT_FOUND' } })
  })
  await page.goto('/')
  await expect(page.getByRole('heading', { name: '登录', exact: true })).toBeVisible()
  await expect(page.getByLabel('演示身份')).toHaveCount(0)
  await page.getByLabel('账号').fill('tester')
  await page.getByLabel('密码').fill('invalid')
  await page.getByLabel('密码').press('Enter')
  await expect(page.getByText('账号或密码不正确', { exact: false })).toBeVisible()
  await expect(page.getByLabel('密码')).toHaveValue('')
  await page.getByLabel('密码').fill('synthetic-test-password')
  await page.getByLabel('密码').press('Enter')
  await expect(page.getByRole('heading', { name: '你的研究项目' })).toBeVisible()
  expect(loginCalls).toBe(2)
  expect(csrfHeaders).toBe(2)
  await page.reload()
  await expect(page.getByRole('heading', { name: '你的研究项目' })).toBeVisible()
  await page.getByRole('button', { name: '退出' }).click()
  await expect(page.getByRole('heading', { name: '登录', exact: true })).toBeVisible()
})

for (const width of [390, 768, 1440]) {
  test(`credential entry remains usable at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 })
    await page.route('**/api/v1/health', route => route.fulfill({ json: { status: 'ok', modes: { a: 'mock', c: 'mock', d: 'mock' }, mock: true, limitations: ['authentication_credentials'] } }))
    await page.route('**/api/v1/auth/me', route => route.fulfill({ status: 401, json: { code: 'UNAUTHENTICATED' } }))
    await page.goto('/')
    await expect(page.getByLabel('账号')).toBeVisible()
    await expect(page.getByLabel('密码')).toBeVisible()
    await expect(page.getByRole('button', { name: '登录', exact: true })).toBeVisible()
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  })
}

test('403 preserves the session and a late project response cannot restore old content after logout', async ({ page }) => {
  let signedIn = true
  let projectsCalls = 0
  let release: (() => void) | undefined
  let projectResponseStarted = false
  const identity = { user: { user_id: '00000000-0000-0000-0000-000000000001', username: 'tester', display_name: 'Tester' }, authentication: 'authentication_credentials', mock: false }
  await page.route('**/api/v1/**', async route => {
    const path = new URL(route.request().url()).pathname
    if (path.endsWith('/health')) return route.fulfill({ json: { status: 'ok', modes: { a: 'mock', c: 'mock', d: 'mock' }, mock: true, limitations: ['authentication_credentials'] } })
    if (path.endsWith('/csrf')) return route.fulfill({ json: { csrf_token: 'synthetic-csrf-token' } })
    if (path.endsWith('/auth/me')) return signedIn ? route.fulfill({ json: identity }) : route.fulfill({ status: 401, json: { code: 'UNAUTHENTICATED' } })
    if (path.endsWith('/auth/logout')) { signedIn = false; return route.fulfill({ json: { message: 'ok' } }) }
    if (path.endsWith('/projects')) {
      if (++projectsCalls === 1) return route.fulfill({ status: 403, json: { code: 'FORBIDDEN' } })
      projectResponseStarted = true
      await new Promise<void>(resolve => { release = resolve })
      return route.fulfill({ json: { items: [{ project_id: 'old-project', name: '旧身份的私有项目', role: 'owner' }] } }).catch(() => {})
    }
    return route.fulfill({ status: 404, json: { code: 'RESOURCE_NOT_FOUND' } })
  })
  await page.goto('/')
  await expect(page.getByRole('heading', { name: '你的研究项目' })).toBeVisible()
  await expect(page.getByText('没有执行此操作的权限', { exact: false })).toBeVisible()
  await page.getByRole('button', { name: '刷新项目' }).click()
  await expect.poll(() => projectResponseStarted).toBe(true)
  await page.getByRole('button', { name: '退出' }).click()
  await expect(page.getByRole('heading', { name: '登录', exact: true })).toBeVisible()
  release?.()
  await expect(page.getByText('旧身份的私有项目')).toHaveCount(0)
})

test('a mutation rechecks an unknown authentication mode after health recovers', async ({ page }) => {
  const project = '00000000-0000-0000-0000-000000000002'
  let healthCalls = 0
  let queryCalls = 0
  let csrfSeen = ''
  await page.route('**/api/v1/**', async route => {
    const path = new URL(route.request().url()).pathname
    if (path.endsWith('/health')) {
      if (++healthCalls === 1) return route.fulfill({ status: 503, json: { code: 'DATA_UNAVAILABLE', message: '服务暂不可用' } })
      return route.fulfill({ json: { status: 'ok', modes: { a: 'persistent', c: 'mock', d: 'real' }, mock: true, limitations: ['authentication_credentials'] } })
    }
    if (path.endsWith('/auth/me')) return route.fulfill({ json: { user: { user_id: '00000000-0000-0000-0000-000000000001', username: 'tester', display_name: 'Tester' }, authentication: 'authentication_credentials', mock: false } })
    if (path.endsWith('/csrf')) return route.fulfill({ json: { csrf_token: 'synthetic-csrf-token' } })
    if (path.endsWith('/secure-queries')) {
      queryCalls++
      csrfSeen = route.request().headers()['x-csrf-token'] || ''
      return route.fulfill({ json: { decision: 'rejected', execution_status: 'not_executed', columns: [], rows: [], reason: { code: 'QUERY_BLOCKED', message: '不可执行' }, policy_id: 'fixture', request_id: '00000000-0000-0000-0000-000000000003', mock: true } })
    }
    if (path.endsWith('/audit-events')) return route.fulfill({ json: { items: [], total: 0 } })
    if (path.endsWith('/projects')) return route.fulfill({ json: { items: [{ project_id: project, name: '测试项目', role: 'owner' }] } })
    return route.fulfill({ status: 404, json: { code: 'RESOURCE_NOT_FOUND' } })
  })
  await page.goto(`/projects/${project}/security`)
  await page.getByRole('button', { name: '提交查询', exact: true }).click()
  await expect.poll(() => queryCalls).toBe(1)
  expect(healthCalls).toBeGreaterThanOrEqual(2)
  expect(csrfSeen).toBe('synthetic-csrf-token')
})
