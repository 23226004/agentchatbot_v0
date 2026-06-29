// 마크다운 렌더 파이프라인 (FE Plan M3): markdown-it → KaTeX 수식 → DOMPurify 새니타이즈.
// Zero-Trust: LLM 출력은 신뢰 불가 → html:false(원시 HTML 비렌더) + DOMPurify(2중 방어).
// [[cite:id]] 는 커스텀 인라인 토큰 → <sup class="cite" data-cite="id">(라벨은 렌더 후 주입).
// 수식: \(...\) 인라인, \[...\]·$$...$$ 디스플레이 → KaTeX(output:'html', throwOnError:false).
// [후속] Mermaid·콜아웃.
import MarkdownIt from 'markdown-it';
import DOMPurify from 'dompurify';
import katex from 'katex';

const md: MarkdownIt = new MarkdownIt({ html: false, linkify: true, breaks: false });

// 수식 인라인 규칙 — 명시적 LaTeX 구분자만 지원(통화 `$100` 오탐 방지: 단일 `$` 미지원).
// 'escape' 앞에 등록해야 `\(`·`\[` 의 백슬래시가 escape 규칙에 먼저 먹히지 않는다.
const MATH_DELIMS: { o: string; c: string; display: boolean }[] = [
  { o: '\\(', c: '\\)', display: false },
  { o: '\\[', c: '\\]', display: true },
  { o: '$$', c: '$$', display: true }
];
md.inline.ruler.before('escape', 'math', (state, silent) => {
  const { src, pos } = state;
  for (const { o, c, display } of MATH_DELIMS) {
    if (!src.startsWith(o, pos)) continue;
    const from = pos + o.length;
    const end = src.indexOf(c, from);
    if (end < 0) return false; // 닫는 구분자 없음 → 수식 아님
    const content = src.slice(from, end);
    if (!content.trim()) return false;
    if (!silent) {
      const token = state.push('math', '', 0);
      token.content = content;
      token.meta = { display };
    }
    state.pos = end + c.length;
    return true;
  }
  return false;
});
md.renderer.rules.math = (tokens, idx) => {
  const t = tokens[idx];
  try {
    return katex.renderToString(t.content, {
      displayMode: (t.meta as { display: boolean }).display,
      throwOnError: false, // 잘못된 수식은 빨간 원문으로(렌더 크래시 방지)
      output: 'html' // MathML 제외 → span 트리만 → DOMPurify 통과 단순화
    });
  } catch {
    return md.utils.escapeHtml(t.content);
  }
};

// [[cite:ID]] 인라인 규칙
md.inline.ruler.before('emphasis', 'cite', (state, silent) => {
  const m = /^\[\[cite:([^\]\s]+)\]\]/.exec(state.src.slice(state.pos));
  if (!m) return false;
  if (!silent) {
    const token = state.push('cite', '', 0);
    token.meta = { id: m[1] };
  }
  state.pos += m[0].length;
  return true;
});
md.renderer.rules.cite = (tokens, idx) => {
  const id = String((tokens[idx].meta as { id: string }).id);
  return `<sup class="cite" data-cite="${md.utils.escapeHtml(id)}"></sup>`;
};

// 링크 안전: 외부 링크는 새 탭 + noopener noreferrer
const renderToken = (tokens: unknown, idx: number, opts: unknown, self: { renderToken: (...a: never[]) => string }) =>
  self.renderToken(tokens as never, idx as never, opts as never);
md.renderer.rules.link_open = (tokens, idx, opts, _env, self) => {
  tokens[idx].attrSet('target', '_blank');
  tokens[idx].attrSet('rel', 'noopener noreferrer');
  return renderToken(tokens, idx, opts, self);
};

export function renderMarkdown(src: string): string {
  const html = md.render(src ?? '');
  return DOMPurify.sanitize(html, { ADD_ATTR: ['target', 'rel', 'data-cite'] });
}
