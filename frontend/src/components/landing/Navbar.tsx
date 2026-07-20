import { useState, useEffect, useRef, useCallback } from "react";
import { Link, useLocation } from "react-router-dom";

const NAV_LINKS = [
  { label: "Features", href: "/#features", isHash: true },
  { label: "Solutions", href: "/#solutions", isHash: true },
  { label: "Showcase", href: "/showcase", isHash: false },
  { label: "Documentation", href: "/docs", isHash: false },
  { label: "Studio", href: "/studio", isHash: false },
] as const;

function Logo() {
  return (
    <Link to="/" className="flex items-center gap-2.5" aria-label="HydraAtlas home">
      <span className="text-[17px] font-semibold tracking-tight text-slate-900">
        HydraAtlas
      </span>
    </Link>
  );
}

function GitHubIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor">
      <path d="M12 0c-6.626 0-12 5.373-12 12 0 5.302 3.438 9.8 8.207 11.387.599.111.793-.261.793-.577v-2.234c-3.338.726-4.033-1.416-4.033-1.416-.546-1.387-1.333-1.756-1.333-1.756-1.089-.745.083-.729.083-.729 1.205.084 1.839 1.237 1.839 1.237 1.07 1.834 2.807 1.304 3.492.997.107-.775.418-1.305.762-1.604-2.665-.305-5.467-1.334-5.467-5.931 0-1.311.469-2.381 1.236-3.221-.124-.303-.535-1.524.117-3.176 0 0 1.008-.322 3.301 1.23A11.509 11.509 0 0112 5.803c1.02.005 2.047.138 3.006.404 2.291-1.552 3.297-1.23 3.297-1.23.653 1.653.242 2.874.118 3.176.77.84 1.235 1.911 1.235 3.221 0 4.609-2.807 5.624-5.479 5.921.43.372.823 1.102.823 2.222v3.293c0 .319.192.694.801.576C20.566 21.797 24 17.3 24 12c0-6.627-5.373-12-12-12z" />
    </svg>
  );
}

function MobileMenu({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const location = useLocation();
  const isFirstRender = useRef(true);

  // Close menu when the route actually changes. We skip the first render
  // (mount) and don't depend on `onClose` itself, since an unstable
  // function reference from the parent would otherwise retrigger this
  // effect on every render and immediately close the menu after opening.
  useEffect(() => {
    if (isFirstRender.current) {
      isFirstRender.current = false;
      return;
    }
    onClose();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location.pathname]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 lg:hidden">
      <div
        className="absolute inset-0 bg-slate-900/20 backdrop-blur-sm"
        onClick={onClose}
        aria-hidden="true"
      />
      <nav className="absolute right-0 top-0 h-full w-72 bg-white px-6 py-6 shadow-xl">
        <div className="flex items-center justify-between mb-8">
          <Logo />
          <button
            type="button"
            onClick={onClose}
            aria-label="Close menu"
            className="flex h-9 w-9 items-center justify-center rounded-lg text-slate-500 transition-colors hover:bg-slate-100 hover:text-slate-700"
          >
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <path d="M18 6L6 18M6 6l12 12" />
            </svg>
          </button>
        </div>
        <div className="flex flex-col gap-2">
          {NAV_LINKS.map((link) => {
            const className = "relative py-2 text-base font-medium text-slate-700 transition-transform duration-200 hover:scale-[1.03] hover:text-slate-900 after:absolute after:bottom-0 after:left-0 after:h-[2px] after:w-full after:origin-left after:scale-x-0 after:bg-blue-600 after:transition-transform after:duration-300 hover:after:scale-x-100 w-fit";

            return link.isHash ? (
              <a key={link.label} href={link.href} onClick={onClose} className={className}>
                {link.label}
              </a>
            ) : (
              <Link key={link.label} to={link.href} className={className}>
                {link.label}
              </Link>
            );
          })}
        </div>
        <div className="mt-6 flex flex-col gap-3 border-t border-slate-100 pt-6">
          <a
            href="https://github.com/rudrapatra23/HydroAtlas.git"
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-2.5 rounded-lg px-3 py-2.5 text-sm font-medium text-slate-600 transition-colors hover:bg-slate-50"
          >
            <GitHubIcon />
            GitHub
          </a>
          <Link
                  to="/studio"
                  className="inline-flex items-center justify-center rounded-xl bg-cyan-400 px-5 py-3 text-sm font-bold text-slate-950 shadow-lg shadow-cyan-500/20 transition hover:bg-cyan-300"
                >
                  Launch Studio
                </Link>
        </div>
      </nav>
    </div>
  );
}

export default function Navbar() {
  const [scrolled, setScrolled] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);

  // Stable reference so MobileMenu's effect doesn't see a "changed" onClose
  // on every Navbar re-render (e.g. right after opening the menu).
  const closeMobileMenu = useCallback(() => setMobileOpen(false), []);

  useEffect(() => {
    const handleScroll = () => setScrolled(window.scrollY > 8);
    window.addEventListener("scroll", handleScroll, { passive: true });
    return () => window.removeEventListener("scroll", handleScroll);
  }, []);

  // Dynamic text classes based on scroll state
  const logoTextClass = scrolled ? "text-slate-900" : "text-white";
  const linkTextClass = scrolled
    ? "text-slate-600 hover:text-slate-900 after:bg-blue-600"
    : "text-slate-300 hover:text-white after:bg-white";
  const iconClass = scrolled ? "text-slate-500 hover:text-slate-700 hover:bg-slate-100" : "text-slate-300 hover:text-white hover:bg-white/10";
  const hamburgerClass = scrolled ? "text-slate-600 hover:bg-slate-100" : "text-white hover:bg-white/10";

  return (
    <>
      <header
        className={`fixed top-0 left-0 right-0 z-40 transition-all duration-300 ${
          scrolled
            ? "bg-white/95 border-b border-slate-200/80 backdrop-blur-md shadow-[0_1px_3px_rgba(0,0,0,0.04)]"
            : "bg-transparent border-b border-transparent"
        }`}
      >
        <div className="mx-auto flex h-16 max-w-7xl items-center justify-between px-6">
          {/* Pass dynamic color down to Logo if needed, or handle it locally via context/props if modified */}
          <Link to="/" className="flex items-center gap-2.5" aria-label="HydraAtlas home">
            <span className={`text-[17px] font-semibold tracking-tight transition-colors duration-300 ${logoTextClass}`}>
              HydraAtlas
            </span>
          </Link>

          {/* Center: Navigation */}
          <nav className="hidden lg:flex items-center gap-6" aria-label="Main navigation">
            {NAV_LINKS.map((link) => {
              const baseClass = "relative py-1 text-[14px] font-medium transition-all duration-200 hover:scale-[1.05] after:absolute after:bottom-0 after:left-0 after:h-[2px] after:w-full after:origin-left after:scale-x-0 after:transition-transform after:duration-300 hover:after:scale-x-100";
              const className = `${baseClass} ${linkTextClass}`;

              return link.isHash ? (
                <a key={link.label} href={link.href} className={className}>
                  {link.label}
                </a>
              ) : (
                <Link key={link.label} to={link.href} className={className}>
                  {link.label}
                </Link>
              );
            })}
          </nav>

          {/* Right: Actions */}
          <div className="hidden lg:flex items-center gap-3">
            <a
              href="https://github.com/rudrapatra23/HydroAtlas.git"
              target="_blank"
              rel="noopener noreferrer"
              aria-label="GitHub repository"
              className={`flex h-9 w-9 items-center justify-center rounded-lg transition-colors duration-300 ${iconClass}`}
            >
              <GitHubIcon />
            </a>
            <Link
              to="/studio"
              className="flex items-center justify-center rounded-lg bg-cyan-400 px-4 py-2.5 text-sm font-bold text-slate-950 transition-colors hover:bg-cyan-300"
            >
              Launch Studio
            </Link>
          </div>

          {/* Mobile: Hamburger */}
          <button
            type="button"
            onClick={() => setMobileOpen(true)}
            aria-label="Open menu"
            className={`flex h-9 w-9 items-center justify-center rounded-lg transition-colors duration-300 lg:hidden ${hamburgerClass}`}
          >
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <path d="M4 6h16M4 12h16M4 18h16" />
            </svg>
          </button>
        </div>
      </header>

      <MobileMenu open={mobileOpen} onClose={closeMobileMenu} />
    </>
  );
}