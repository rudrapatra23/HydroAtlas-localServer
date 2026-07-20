import { Link, useNavigate } from "react-router-dom";
import Footer from "../components/landing/Footer";
import Navbar from "../components/landing/Navbar";
import { useToast } from "../context/ToastContext";

const metrics = [
  { value: "0.1°", label: "ERA5-Land grid resolution" },
  { value: "3", label: "core hydrology signals" },
  { value: "30d", label: "rolling local time series" },
];

const features = [
  {
    title: "Climate layers that load fast",
    description:
      "Explore rainfall, soil moisture, and runoff from one map-first workspace.",
    // Custom layered maps icon
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="h-full w-full">
        <path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5" />
      </svg>
    ),
  },
  {
    title: "Point-click hydrology context",
    description:
      "Select a location and inspect local trends without moving raw rasters around.",
    // Custom crosshair / target analytics icon
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="h-full w-full">
        <circle cx="12" cy="12" r="10" />
        <path d="M12 2v4M12 18v4M2 12h4M18 12h4M12 9a3 3 0 100 6 3 3 0 000-6z" />
      </svg>
    ),
  },
  {
    title: "Local-first data pipeline",
    description:
      "FastAPI, SQLite metadata, and local storage keep the stack portable and transparent.",
    // Custom database / cluster pipeline icon
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="h-full w-full">
        <path d="M12 22c5.523 0 10-2.239 10-5s-4.477-5-10-5-10 2.239-10 5 4.477 5 10 5z" />
        <path d="M2 12c0 2.761 4.477 5 10 5s10-2.239 10-5" />
        <path d="M2 7c0 2.761 4.477 5 10 5s10-2.239 10-5" />
      </svg>
    ),
  },
];

const workflow = [
  "Ingest ERA5-Land data",
  "Index spatial metadata",
  "Explore signals in Studio",
];

export default function LandingPage() {
  const navigate = useNavigate();
  const { toast } = useToast();
  return (
    <div className="min-h-screen bg-slate-950 text-white">
      <Navbar />

      <main>
        <section className="relative isolate overflow-hidden px-6 pt-32 pb-20 sm:pt-40 sm:pb-28">
          <div className="absolute inset-0 -z-10 bg-slate-950 bg-[radial-gradient(circle_at_20%_20%,rgba(14,165,233,0.24),transparent_30%),radial-gradient(circle_at_80%_0%,rgba(16,185,129,0.16),transparent_28%)]" />
          <div
            className="absolute inset-x-0 top-0 -z-10 h-[560px] opacity-[0.08]"
            style={{
              backgroundImage:
                "linear-gradient(rgba(255,255,255,.7) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,.7) 1px, transparent 1px)",
              backgroundSize: "56px 56px",
            }}
            aria-hidden="true"
          />

          <div className="mx-auto grid max-w-7xl items-center gap-14 lg:grid-cols-[1fr_1.1fr]">
            <div>
              <div className="inline-flex items-center gap-2 rounded-full border border-cyan-300/20 bg-white/10 px-3 py-1 text-xs font-semibold text-cyan-100 shadow-sm backdrop-blur">
                <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
                Hydrology intelligence for India
              </div>

              <h1 className="mt-6 max-w-4xl text-4xl font-black tracking-[-0.05em] text-white sm:text-6xl">
                Hydrology, visualized with clarity.
              </h1>

              <p className="mt-5 max-w-2xl text-base leading-7 text-slate-300 sm:text-lg sm:leading-8">
                Explore rainfall, soil moisture, and surface runoff through
                interactive maps, time-series analytics, and district-level
                insights powered by ERA5-Land climate data.
              </p>

              <div className="mt-9 flex flex-col gap-3 sm:flex-row">
                <Link
                  to="/studio"
                  className="inline-flex items-center justify-center rounded-xl bg-cyan-400 px-5 py-3 text-sm font-bold text-slate-950 shadow-lg shadow-cyan-500/20 transition hover:bg-cyan-300"
                >
                  Launch Studio
                </Link>
                <Link
                  to="/docs"
                  className="inline-flex items-center justify-center rounded-xl border border-white/15 bg-white/10 px-5 py-3 text-sm font-bold text-white backdrop-blur transition hover:bg-white/15"
                >
                  View docs
                </Link>
              </div>

              <div className="mt-10 grid max-w-xl grid-cols-3 gap-3">
                {metrics.map((metric) => (
                  <div
                    key={metric.label}
                    className="rounded-2xl border border-white/10 bg-white/[0.07] p-4 backdrop-blur"
                  >
                    <p className="text-2xl font-black tracking-tight text-white">
                      {metric.value}
                    </p>
                    <p className="mt-1 text-xs leading-5 text-slate-400">
                      {metric.label}
                    </p>
                  </div>
                ))}
              </div>
            </div>

            {/* Faux Workspace Column Container */}
            <div 
              role="button"
              tabIndex={0}
              onClick={() => {
                toast("Do you want to visit the Studio?", {
                  description: "You're about to open the interactive hydrology workspace.",
                  type: "confirm",
                  position: "top-center",
                  action: {
                    label: "Launch Studio",
                    onClick: () => navigate("/studio"),
                  },
                  cancelAction: {
                    label: "Cancel",
                    onClick: () => {},
                  },
                });
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  toast("Do you want to visit the Studio?", {
                    description: "You're about to open the interactive hydrology workspace.",
                    type: "confirm",
                    position: "top-center",
                    action: {
                      label: "Launch Studio",
                      onClick: () => navigate("/studio"),
                    },
                    cancelAction: {
                      label: "Cancel",
                      onClick: () => {},
                    },
                  });
                }
              }}
              className="group relative w-full cursor-pointer transition-all duration-300 hover:scale-[1.01] rounded-[2rem] border border-white/10 bg-white/5 p-3 shadow-2xl shadow-slate-950/50 backdrop-blur"
            >
              <div className="overflow-hidden rounded-[1.5rem] border border-slate-800 bg-slate-950">
                {/* Mac window header */}
                <div className="flex items-center gap-2 border-b border-white/10 bg-slate-900/80 px-5 py-3">
                  <div className="h-3 w-3 rounded-full bg-red-500/90"></div>
                  <div className="h-3 w-3 rounded-full bg-yellow-500/90"></div>
                  <div className="h-3 w-3 rounded-full bg-green-500/90"></div>
                  <div className="ml-4 text-[11px] font-medium tracking-wide text-slate-400">localhost:3000/studio</div>
                </div>

                {/* The aspect-ratio viewport chamber */}
                <div className="relative h-[480px] w-full overflow-hidden bg-slate-900">
                  {/* Invisible click shield wrapper */}
                  <div className="absolute inset-0 z-20" />
                  
                  {/* Hover visual enhancement indicator */}
                  <div className="absolute inset-0 z-10 flex items-center justify-center bg-slate-950/0 transition-colors group-hover:bg-slate-950/20">
                    <span className="rounded-full bg-white/10 px-4 py-2 text-xs font-semibold text-white opacity-0 backdrop-blur-md transition-opacity group-hover:opacity-100 border border-white/10">
                      Click to enter workspace
                    </span>
                  </div>

                  {/* 
                    High-Fidelity Canvas Scale:
                    Renders an explicit 1280px wide screen dimension to force a desktop layout engine, 
                    then scales down seamlessly via transform: scale() to match the parent layout boundary.
                  */}
                  <div className="absolute left-0 top-0 origin-top-left h-[738px] w-[1280px] scale-[0.43] sm:scale-[0.52] md:scale-[0.60] lg:scale-[0.45] xl:scale-[0.56]">
                    <iframe 
                      src="/studio" 
                      title="Studio Preview" 
                      className="h-full w-full border-0 pointer-events-none select-none" 
                      tabIndex={-1} 
                      aria-hidden="true" 
                    />
                  </div>
                </div>
              </div>
            </div>

          </div>
        </section>

        <section id="features" className="bg-white px-6 py-20 text-slate-950">
          <div className="mx-auto max-w-7xl">
            <div className="max-w-2xl">
              <p className="text-sm font-bold uppercase tracking-[0.2em] text-cyan-600">
                Product
              </p>
              <h2 className="mt-3 text-3xl font-black tracking-tight sm:text-5xl">
                Built like SaaS. Grounded like science.
              </h2>
            </div>

            <div className="mt-12 grid gap-5 md:grid-cols-3">
              {features.map((feature) => (
                <article
                  key={feature.title}
                  className="rounded-3xl border border-slate-200 bg-slate-50 p-6 transition hover:-translate-y-1 hover:border-cyan-200 hover:bg-white hover:shadow-xl hover:shadow-slate-200/70"
                >
                  <div className="mb-6 h-12 w-12 rounded-2xl bg-slate-950 p-3.5 text-cyan-300">
                    {feature.icon}
                  </div>
                  <h3 className="text-lg font-bold text-slate-950">
                    {feature.title}
                  </h3>
                  <p className="mt-3 text-sm leading-7 text-slate-600">
                    {feature.description}
                  </p>
                </article>
              ))}
            </div>
          </div>
        </section>

        <section
          id="solutions"
          className="bg-slate-50 px-6 py-20 text-slate-950"
        >
          <div className="mx-auto grid max-w-7xl gap-12 lg:grid-cols-[0.85fr_1.15fr]">
            <div>
              <p className="text-sm font-bold uppercase tracking-[0.2em] text-cyan-600">
                Workflow
              </p>
              <h2 className="mt-3 text-3xl font-black tracking-tight sm:text-5xl">
                One clean loop from climate data to operational insight.
              </h2>
              <p className="mt-5 text-base leading-8 text-slate-600">
                Keep the story simple: fetch trustworthy data, index it locally,
                and give teams a beautiful place to reason about water.
              </p>
            </div>

            <div className="grid gap-4">
              {workflow.map((step, index) => (
                <div
                  key={step}
                  className="flex items-center gap-5 rounded-3xl border border-slate-200 bg-white p-5 shadow-sm"
                >
                  <span className="flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl bg-cyan-50 text-sm font-black text-cyan-700">
                    0{index + 1}
                  </span>
                  <div>
                    <h3 className="font-bold text-slate-950">{step}</h3>
                    <p className="mt-1 text-sm text-slate-500">
                      Clear enough for operators, transparent enough for
                      researchers.
                    </p>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </section>

        <section className="bg-white px-6 py-20 text-slate-950">
          <div className="mx-auto flex max-w-7xl flex-col items-start justify-between gap-8 rounded-[2rem] bg-slate-950 p-8 text-white shadow-2xl shadow-slate-200 sm:p-10 lg:flex-row lg:items-center">
            <div>
              <p className="text-sm font-bold uppercase tracking-[0.2em] text-cyan-300">
                Ready when you are
              </p>
              <h2 className="mt-3 max-w-2xl text-3xl font-black tracking-tight sm:text-4xl">
                Open the Studio and start exploring hydrology signals.
              </h2>
            </div>
            <Link
              to="/studio"
              className="inline-flex shrink-0 items-center justify-center rounded-xl bg-cyan-400 px-5 py-3 text-sm font-bold text-slate-950 transition hover:bg-cyan-300"
            >
              Launch Studio
            </Link>
          </div>
        </section>
      </main>

      <Footer />
    </div>
  );
}