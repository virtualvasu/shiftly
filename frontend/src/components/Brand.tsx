/** Shiftly brand: a route from pickup to drop, and the wordmark. */
export function LogoMark({ className = "size-7" }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" className={className} aria-hidden="true">
      <rect width="32" height="32" rx="9" fill="var(--primary)" />
      <path d="M9 22c0-6 14-6 14-12" fill="none" stroke="var(--amber)" strokeWidth="2.6" strokeLinecap="round" />
      <circle cx="9" cy="22" r="3" fill="#fff" />
      <circle cx="23" cy="10" r="3" fill="var(--amber)" />
    </svg>
  );
}

export function Brand({ onClick, size = "md" }: { onClick?: () => void; size?: "md" | "lg" }) {
  const content = (
    <>
      <LogoMark className={size === "lg" ? "size-9" : "size-7"} />
      <span className={"font-display font-bold tracking-tight " + (size === "lg" ? "text-2xl" : "text-lg")}>shiftly</span>
    </>
  );
  if (!onClick) return <span className="inline-flex items-center gap-2">{content}</span>;
  return (
    <button onClick={onClick} className="inline-flex items-center gap-2 rounded-md focus-visible:outline-2 focus-visible:outline-ring" aria-label="Shiftly home">
      {content}
    </button>
  );
}
