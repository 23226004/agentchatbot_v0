<script lang="ts">
  import '$lib/shared/ui/tokens.css';
  import 'katex/dist/katex.min.css'; // 수식 렌더 스타일(KaTeX)
  let { children } = $props();
</script>

{@render children()}
