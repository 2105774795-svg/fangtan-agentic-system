import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { '/api': 'http://127.0.0.1:18995', '/platform-media': 'http://127.0.0.1:18995' } },
  build: { outDir: '../backend/static/platform', emptyOutDir: true, assetsDir: 'platform-assets', rollupOptions: { output: { manualChunks: { graph: ['@xyflow/react'] } } } },
});
