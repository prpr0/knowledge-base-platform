/** 配置本地控制台服务及真实API代理。 */
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

/** 本地控制台与真实 API 的开发入口。 */
export default defineConfig({
  plugins: [vue()],
  server: {
    host: '127.0.0.1',
    port: 5176,
    strictPort: true,
    proxy: { '/api': { target: 'http://127.0.0.1:8016', changeOrigin: true } },
  },
})
