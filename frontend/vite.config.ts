import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

export default defineConfig({
  plugins: [sveltekit()],
  // 5173 충돌 회피 — dev/preview 모두 5180 사용.
  server: { port: 5180 },
  preview: { port: 5180 }
});
