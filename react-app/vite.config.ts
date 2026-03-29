declare const process: { env: Record<string, string | undefined> };
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    host: true,
    sourcemapIgnoreList: false,
    proxy: {
      // Forward gateway endpoints to the local gateway process.
      // This mirrors what nginx does in production, allowing the same
      // VITE_GATEWAY_URL=/ setting to work in both dev and prod.
      '/sse': {
        target: `http://localhost:${process.env['GATEWAY_PORT'] ?? '3001'}`,
        changeOrigin: true,
        // SSE requires no buffering
        configure: (proxy: any) => {
          proxy.on('proxyReq', (proxyReq: any) => {
            proxyReq.setHeader('X-Forwarded-Proto', 'http');
          });
        },
      },
      '/message': {
        target: `http://localhost:${process.env['GATEWAY_PORT'] ?? '3001'}`,
        changeOrigin: true,
      },
      '/register-catalogs': {
        target: `http://localhost:${process.env['GATEWAY_PORT'] ?? '3001'}`,
        changeOrigin: true,
      },
      '/register-surface': {
        target: `http://localhost:${process.env['GATEWAY_PORT'] ?? '3001'}`,
        changeOrigin: true,
      },
      '/send': {
        target: `http://localhost:${process.env['GATEWAY_PORT'] ?? '3001'}`,
        changeOrigin: true,
      },
    },
  },
  build: {
    sourcemap: true
  },
});
