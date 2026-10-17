import Markdown, { defaultUrlTransform } from 'react-markdown';
import remarkGfm from 'remark-gfm';

// Balance a still-arriving emphasis delimiter in the view only; preserve the source.
function streamingSource(source: string) {
  let fenced = false;
  return source.split('\n').map(line => {
    if (/^\s*```/.test(line)) { fenced = !fenced; return line; }
    if (fenced) return line;
    let text = line.replace(/(?<!\\)(?<!\*)\*$/, '');
    const markers = text.match(/(?<!\\)\*\*/g) || [];
    if (markers.length % 2) text = text.endsWith('**') ? text.slice(0, -2) : text + '**';
    return text;
  }).join('\n');
}

export function MarkdownContent({ text, streaming = false, onReference }: { text: string; streaming?: boolean; onReference?: (reference: string) => void }) {
  let source = streaming ? streamingSource(text) : text;
  if (onReference) source = source.replace(/\[(M\d+-P\d+)\](?!\()/g, '[$1](evidence:$1)');
  return <div className="rendered-markdown"><Markdown remarkPlugins={[remarkGfm]} skipHtml urlTransform={url => /^evidence:M\d+-P\d+$/.test(url) && onReference ? url : defaultUrlTransform(url)} components={{
    a: ({ href, children }) => href?.startsWith('evidence:') && onReference
      ? <button className="evidence-reference" onClick={() => onReference('[' + href.slice(9) + ']')}>[{children}]</button>
      : <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>,
    table: ({ children }) => <div className="markdown-table-scroll"><table>{children}</table></div>,
  }}>{source}</Markdown></div>;
}
