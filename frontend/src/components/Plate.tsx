/** A cab number styled like an Indian registration plate. */
export function Plate({ number }: { number: string }) {
  return (
    <span className="inline-flex items-center rounded-[3px] border border-foreground/70 bg-card px-1.5 py-0.5 font-mono text-[11px] font-semibold tracking-wider text-foreground">
      {number}
    </span>
  );
}
