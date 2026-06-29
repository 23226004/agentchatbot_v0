import adapter from '@sveltejs/adapter-static';
import { vitePreprocess } from '@sveltejs/vite-plugin-svelte';

// SPA 모드: 별도 Python 백엔드를 보는 순수 클라이언트. fallback 으로 클라이언트 라우팅.
export default {
  preprocess: vitePreprocess(),
  kit: {
    adapter: adapter({ fallback: 'index.html' })
  }
};
