import { HydraLogo } from "@/components/ui/HydraLogo";

/**
 * Top navigation bar. Floats over the map with a glass background.
 * Layout:
 *   - Left: logo + product name
 *   - Center: primary navigation (hidden below md breakpoint)
 *   - Right: status pill + sign-in CTA
 */
export function Navbar() {
  return (
    <header className="glass fixed inset-x-3 top-3 z-30 flex h-16 items-center justify-between rounded-3xl border border-white/10 px-4 shadow-[0_12px_40px_rgba(3,12,24,0.24)] backdrop-blur-xl md:inset-x-4 md:px-5">
      <div className="flex items-center gap-3.5">
        <div className="rounded-2xl bg-cyan-400/10 p-1.5 ring-1 ring-cyan-300/20">
          <HydraLogo className="h-7 w-7" />
        </div>
        <div className="flex flex-col leading-tight">
          <span className="text-sm font-medium tracking-[0.02em] text-white/95">
            HydraAtlas
          </span>
          <span className="text-[11px] font-normal tracking-[0.08em] text-slate-300/80">
            Earth Observation Workspace
          </span>
        </div>
      </div>

      <nav className="hidden items-center gap-1.5 md:flex" aria-label="Primary">
        {[
          { label: "Explore", active: true },
          { label: "Datasets" },
          { label: "Analytics" },
          { label: "Docs" },
        ].map((item) => (
          <a
            key={item.label}
            href="#"
            className={
              "rounded-full px-3.5 py-2 text-sm font-normal tracking-[0.01em] transition-colors duration-300 " +
              (item.active
                ? "bg-cyan-400/12 text-cyan-100 ring-1 ring-cyan-300/20"
                : "text-slate-300/78 hover:bg-white/6 hover:text-white")
            }
          >
            {item.label}
          </a>
        ))}
      </nav>

      <div className="flex items-center gap-2.5">
        <span className="hidden items-center gap-1.5 rounded-full border border-emerald-300/18 bg-emerald-400/10 px-3 py-1.5 text-[11px] font-medium tracking-[0.02em] text-emerald-100 sm:flex">
          <span className="h-2 w-2 rounded-full bg-emerald-300 shadow-[0_0_12px_rgba(110,231,183,0.65)]" />
          Mock data
        </span>
        <button
          type="button"
          className="rounded-full border border-cyan-300/25 bg-cyan-400/14 px-4 py-2 text-sm font-medium text-cyan-50 transition duration-300 hover:bg-cyan-400/20"
        >
          Sign in
        </button>
      </div>
    </header>
  );
}
