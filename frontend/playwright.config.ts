import { defineConfig } from '@playwright/test'
import path from 'node:path'
import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
const python = path.resolve('..', '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
const apiPort = Number(process.env.PAPERPILOT_E2E_API_PORT || 8000)
const webPort = Number(process.env.PAPERPILOT_E2E_WEB_PORT || 5173)
const dataMode = process.env.PAPERPILOT_E2E_DATA_MODE || 'mock'
const testStorage = mkdtempSync(path.join(tmpdir(), 'paperpilot-e2e-'))
export default defineConfig({
  testDir: './tests', fullyParallel: false, workers: 1, timeout: 30000,
  use: { baseURL: `http://127.0.0.1:${webPort}`, headless: true, viewport: { width: 1440, height: 1000 } },
  webServer: [
    { command: `"${python}" -m uvicorn app.main:create_app --factory --app-dir backend --host 127.0.0.1 --port ${apiPort} --no-access-log`, cwd: '..', url: `http://127.0.0.1:${apiPort}/api/v1/health`, reuseExistingServer: false, env: { PAPERPILOT_ENV: 'test', PAPERPILOT_AUTH_MODE: 'development', PAPERPILOT_A_MODE: dataMode, PAPERPILOT_C_MODE: 'mock', PAPERPILOT_D_MODE: 'mock', PAPERPILOT_ALLOWED_ORIGINS: `http://127.0.0.1:${webPort}`, PAPERPILOT_STORAGE_DIR: path.join(testStorage, 'uploads') } },
    { command: `npm run dev -- --port ${webPort}`, url: `http://127.0.0.1:${webPort}`, reuseExistingServer: false, env: { PAPERPILOT_PROXY_TARGET: `http://127.0.0.1:${apiPort}` } },
  ],
})
