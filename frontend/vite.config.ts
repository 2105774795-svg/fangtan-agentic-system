import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

// 将模型及与锁文件完全一致的运行时一起发布，兼容现有 /platform-assets 挂载。
const vadAssets = [
  ['@ricky0123/vad-web/dist', 'vad.worklet.bundle.min.js'],
  ['@ricky0123/vad-web/dist', 'silero_vad_v5.onnx'],
  ['onnxruntime-web/dist', 'ort-wasm-simd-threaded.mjs'],
  ['onnxruntime-web/dist', 'ort-wasm-simd-threaded.wasm'],
].map(([folder, name]) => ({ name, path: fileURLToPath(new URL(`./node_modules/${folder}/${name}`, import.meta.url)) }));

export default defineConfig({
  plugins: [react(), {
    name: 'local-conversation-vad',
    generateBundle() {
      for (const asset of vadAssets) this.emitFile({ type: 'asset', fileName: `platform-assets/vad/${asset.name}`, source: readFileSync(asset.path) });
    },
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const asset = vadAssets.find(asset => req.url?.split('?')[0] === `/platform-assets/vad/${asset.name}`);
        if (!asset) return next();
        res.setHeader('Content-Type', asset.name.endsWith('.wasm') ? 'application/wasm' : asset.name.endsWith('.js') || asset.name.endsWith('.mjs') ? 'text/javascript' : 'application/octet-stream');
        res.end(readFileSync(asset.path));
      });
    },
  }],
  server: { port: 5173, proxy: { '/assets/recording-support.js': 'http://127.0.0.1:18995', '/api': 'http://127.0.0.1:18995', '/platform-media': 'http://127.0.0.1:18995' } },
  build: { outDir: '../backend/static/platform', emptyOutDir: true, assetsDir: 'platform-assets', rollupOptions: { output: { manualChunks: { markdown: ['react-markdown', 'remark-gfm'], graph: ['@xyflow/react'] } } } },
});
