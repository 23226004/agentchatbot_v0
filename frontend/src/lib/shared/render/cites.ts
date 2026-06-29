// 본문의 [[cite:id]] 토큰을 분리한다. (M3 풀 마크다운 렌더의 최소 선행 조각)
export type Segment = { t: 'text'; v: string } | { t: 'cite'; id: string };

export function splitCites(text: string): Segment[] {
  const re = /\[\[cite:([^\]]+)\]\]/g;
  const out: Segment[] = [];
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push({ t: 'text', v: text.slice(last, m.index) });
    out.push({ t: 'cite', id: m[1] });
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push({ t: 'text', v: text.slice(last) });
  return out;
}
