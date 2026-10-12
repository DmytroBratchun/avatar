/** A small "?" next to a heading or a label: what the thing does, shown on hover
 * (and to screen readers), so the page itself carries only what changes. */
export default function Help({ text }: { text: string }) {
  return (
    <span className="help" title={text} aria-label={text} role="img" tabIndex={0}>
      ?
    </span>
  );
}
