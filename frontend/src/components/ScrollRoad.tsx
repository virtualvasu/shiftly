import { useEffect, useState } from "react";

/** A thin road under the header; the cab drives along it as you scroll the page. */
export function ScrollRoad() {
  const [progress, setProgress] = useState(0);
  const [scrollY, setScrollY] = useState(0);

  useEffect(() => {
    let frame = 0;
    const update = () => {
      frame = 0;
      const max = document.documentElement.scrollHeight - window.innerHeight;
      setProgress(max > 0 ? Math.min(1, window.scrollY / max) : 0);
      setScrollY(window.scrollY);
    };
    const onScroll = () => {
      if (!frame) frame = requestAnimationFrame(update);
    };
    update();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    return () => {
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
      cancelAnimationFrame(frame);
    };
  }, []);

  const wheel = { transform: `rotate(${scrollY * 1.5}deg)`, transformBox: "fill-box" as const, transformOrigin: "center" };

  return (
    <div aria-hidden="true" className="relative h-5 overflow-hidden border-t bg-muted">
      <div className="absolute inset-x-0 top-1/2 h-px -translate-y-1/2 bg-[repeating-linear-gradient(90deg,var(--muted-foreground)_0_10px,transparent_10px_22px)] opacity-40" />
      <svg
        viewBox="0 0 44 20"
        className="absolute top-0 h-5 w-11"
        style={{ left: `calc(${progress} * (100% - 2.75rem))` }}
      >
        <path d="M4 13V9.5c0-1 .6-1.8 1.6-2.1L12 5.5 15.5 2h12l4.5 4.5 7.2 1.6c1 .2 1.8 1.1 1.8 2.2V13z" fill="var(--amber)" />
        <path d="M16.5 3.5h5v4h-8.3zM23 3.5h4.2l3.6 4H23z" fill="var(--primary)" opacity=".85" />
        <rect x="18" y="0.5" width="5" height="1.6" rx=".6" fill="var(--primary)" />
        <g style={wheel}>
          <circle cx="11" cy="14.5" r="3.4" fill="var(--foreground)" />
          <path d="M11 11.6v5.8M8.1 14.5h5.8" stroke="var(--muted)" strokeWidth=".9" />
        </g>
        <g style={wheel}>
          <circle cx="33" cy="14.5" r="3.4" fill="var(--foreground)" />
          <path d="M33 11.6v5.8M30.1 14.5h5.8" stroke="var(--muted)" strokeWidth=".9" />
        </g>
      </svg>
    </div>
  );
}
