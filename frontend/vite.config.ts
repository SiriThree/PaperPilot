import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { readFileSync } from 'node:fs'

const tlsCert = process.env.PAPERPILOT_DEV_TLS_CERT
const tlsKey = process.env.PAPERPILOT_DEV_TLS_KEY
if (!!tlsCert !== !!tlsKey) throw new Error('Configure both development TLS certificate and key')

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173, strictPort: true,
    https: tlsCert && tlsKey ? { cert: readFileSync(tlsCert), key: readFileSync(tlsKey) } : undefined,
    proxy: { '/api': process.env.PAPERPILOT_PROXY_TARGET || 'http://127.0.0.1:8000' },
  },
})
