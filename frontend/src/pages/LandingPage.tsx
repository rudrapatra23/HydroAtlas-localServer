import { Link } from "react-router-dom";
import Navbar from "../components/landing/Navbar";
import Footer from "../components/landing/Footer";

/**
 * Landing page — marketing home for HydraAtlas.
 * Phase 1: Navbar + Hero placeholder + Footer.
 * Phase 2 will add feature sections, dataset showcase, and CTAs.
 */
export default function LandingPage() {
  return (
    <div className="min-h-screen bg-white">
      <Navbar />

      {/* ─── Hero Section ─── */}
      <section className="relative overflow-hidden bg-[#0A0E27] pt-32 pb-24 sm:pt-40 sm:pb-32">
        {/* Subtle grid overlay */}
        <div
          className="pointer-events-none absolute inset-0 opacity-[0.03]"
          style={{
            backgroundImage:
              "linear-gradient(rgba(255,255,255,.5) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,.5) 1px, transparent 1px)",
            backgroundSize: "64px 64px",
          }}
          aria-hidden="true"
        />

        {/* Radial glow */}
        <div
          className="pointer-events-none absolute top-0 left-1/2 -translate-x-1/2 h-[600px] w-[900px] opacity-30"
          style={{
            background:
              "radial-gradient(ellipse at center, rgba(37,99,235,0.35) 0%, transparent 70%)",
          }}
          aria-hidden="true"
        />

        <div className="relative mx-auto max-w-4xl px-6 text-center">
          {/* Badge */}
          <div className="mb-8 inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/5 px-4 py-1.5">
            <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
            <span className="text-xs font-medium text-slate-300">
              Powered by ERA5-Land &amp; Copernicus
            </span>
          </div>

          <h1 className="text-4xl font-extrabold tracking-tight text-white sm:text-6xl lg:text-7xl">
            Climate Intelligence
            <br />
            <span className="text-blue-400">for Hydrology</span>
          </h1>

          <p className="mx-auto mt-6 max-w-2xl text-lg leading-relaxed text-slate-400">
            Analyze rainfall, soil moisture, and surface runoff across India with
            satellite-grade precision. Built on ERA5-Land reanalysis data, served
            through a modern geospatial stack.
          </p>

          {/* CTA Buttons */}
          <div className="mt-10 flex flex-col items-center gap-4 sm:flex-row sm:justify-center">
            <Link
              to="/studio"
              className="inline-flex items-center gap-2 rounded-lg bg-blue-600 px-6 py-3 text-sm font-semibold text-white transition-colors hover:bg-blue-500"
            >
              Launch Studio
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
                <path d="M6 3l5 5-5 5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </Link>
            <Link
              to="/docs"
              className="inline-flex items-center gap-2 rounded-lg border border-white/15 bg-white/5 px-6 py-3 text-sm font-semibold text-slate-300 transition-colors hover:bg-white/10 hover:text-white"
            >
              Read Documentation
            </Link>
          </div>

          {/* Hero visual placeholder — sized for future screenshot/video */}
          <div className="relative mt-16 overflow-hidden rounded-xl border border-white/10 bg-white/[0.03] shadow-2xl">
            <div className="aspect-video flex items-center justify-center">
              <div className="text-center">
                <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-xl bg-blue-600/20">
                  <svg width="28" height="28" viewBox="0 0 24 24" fill="none">
                    <path d="M12 2L4 7v10l8 5 8-5V7l-8-5z" stroke="#60A5FA" strokeWidth="1.5" strokeLinejoin="round" />
                    <path d="M12 22V12" stroke="#60A5FA" strokeWidth="1.5" />
                    <path d="M4 7l8 5 8-5" stroke="#60A5FA" strokeWidth="1.5" />
                    <circle cx="12" cy="12" r="2" fill="#60A5FA" />
                  </svg>
                </div>
                <p className="text-sm font-medium text-slate-500">
                  Interactive Studio Preview
                </p>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* ─── Trusted Technologies ─── */}
      <section className="border-b border-slate-100 bg-white py-16">
        <div className="mx-auto max-w-5xl px-6">
          <p className="text-center text-xs font-semibold uppercase tracking-wider text-slate-400">
            Built on trusted technologies
          </p>
          <div className="mt-8 flex flex-wrap items-center justify-center gap-x-12 gap-y-6">
            {[
              "ERA5-Land",
              "Copernicus CDS",
              "MapLibre GL",
              "FastAPI",
              "SQLite",
              "Local Storage",
            ].map((tech) => (
              <span
                key={tech}
                className="text-sm font-semibold tracking-tight text-slate-400 transition-colors hover:text-slate-600"
              >
                {tech}
              </span>
            ))}
          </div>
        </div>
      </section>

      {/* ─── Features Anchor ─── */}
      <section id="features" className="py-24 sm:py-32">
        <div className="mx-auto max-w-7xl px-6">
          <div className="mx-auto max-w-2xl text-center">
            <p className="text-sm font-medium tracking-wide text-blue-600 uppercase">
              Features
            </p>
            <h2 className="mt-3 text-3xl font-bold tracking-tight text-slate-900 sm:text-4xl">
              Everything you need for hydrological analysis
            </h2>
            <p className="mt-4 text-lg leading-relaxed text-slate-600">
              From raw satellite data to actionable insights — one platform,
              zero infrastructure.
            </p>
          </div>

          {/* Feature cards — will be expanded in Phase 2 */}
          <div className="mt-16 grid gap-8 sm:grid-cols-2 lg:grid-cols-3">
            {[
              {
                title: "Rainfall Analytics",
                description: "Spatiotemporal rainfall analysis with ERA5-Land reanalysis at 0.1° resolution. Trend detection, anomaly alerts, and seasonal decomposition.",
                icon: (
                  <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M12 2.69l5.66 5.66a8 8 0 11-11.31 0z" />
                  </svg>
                ),
              },
              {
                title: "Soil Moisture Mapping",
                description: "Volumetric soil water content across multiple depth layers. Critical for drought monitoring, agriculture planning, and flood forecasting.",
                icon: (
                  <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M2 12h20M2 12c0 5.523 4.477 10 10 10s10-4.477 10-10M2 12C2 6.477 6.477 2 12 2s10 4.477 10 10" />
                    <path d="M12 2a15.3 15.3 0 014 10 15.3 15.3 0 01-4 10 15.3 15.3 0 01-4-10 15.3 15.3 0 014-10z" />
                  </svg>
                ),
              },
              {
                title: "Surface Runoff",
                description: "Quantify surface and subsurface runoff volumes. Understand water balance dynamics and identify flood-prone regions before events occur.",
                icon: (
                  <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M2 6c.6.5 1.2 1 2.5 1C7 7 7 5 9.5 5c2.6 0 2.4 2 5 2 2.5 0 2.5-2 5-2 1.3 0 1.9.5 2.5 1" />
                    <path d="M2 12c.6.5 1.2 1 2.5 1 2.5 0 2.5-2 5-2 2.6 0 2.4 2 5 2 2.5 0 2.5-2 5-2 1.3 0 1.9.5 2.5 1" />
                    <path d="M2 18c.6.5 1.2 1 2.5 1 2.5 0 2.5-2 5-2 2.6 0 2.4 2 5 2 2.5 0 2.5-2 5-2 1.3 0 1.9.5 2.5 1" />
                  </svg>
                ),
              },
              {
                title: "Interactive Map Studio",
                description: "MapLibre-powered GIS interface with layer management, coordinate search, and point-click data retrieval. Pan, zoom, and explore freely.",
                icon: (
                  <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0118 0z" />
                    <circle cx="12" cy="10" r="3" />
                  </svg>
                ),
              },
              {
                title: "Time Series & Trends",
                description: "30-day rolling time series for every grid point. Statistical summaries, trend lines, and exportable datasets in CSV and JSON formats.",
                icon: (
                  <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M3 3v18h18" />
                    <path d="M18 17l-5-9-4 5-3-3" />
                  </svg>
                ),
              },
              {
                title: "Local-First Architecture",
                description: "FastAPI backend, SQLite metadata, local raster storage, and a React frontend. Easy to run on a fresh machine with minimal setup.",
                icon: (
                  <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M18 10h-1.26A8 8 0 109 20h9a5 5 0 000-10z" />
                  </svg>
                ),
              },
            ].map((feature) => (
              <div
                key={feature.title}
                className="group rounded-xl border border-slate-200 bg-white p-6 transition-all duration-200 hover:border-slate-300 hover:shadow-sm"
              >
                <div className="mb-4 flex h-10 w-10 items-center justify-center rounded-lg bg-blue-50 text-blue-600 transition-colors group-hover:bg-blue-100">
                  {feature.icon}
                </div>
                <h3 className="text-base font-semibold text-slate-900">
                  {feature.title}
                </h3>
                <p className="mt-2 text-sm leading-relaxed text-slate-600">
                  {feature.description}
                </p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ─── Solutions Anchor ─── */}
      <section id="solutions" className="border-t border-slate-100 bg-slate-50 py-24 sm:py-32">
        <div className="mx-auto max-w-7xl px-6">
          <div className="mx-auto max-w-2xl text-center">
            <p className="text-sm font-medium tracking-wide text-blue-600 uppercase">
              Solutions
            </p>
            <h2 className="mt-3 text-3xl font-bold tracking-tight text-slate-900 sm:text-4xl">
              From satellite to decision
            </h2>
            <p className="mt-4 text-lg leading-relaxed text-slate-600">
              HydraAtlas bridges the gap between raw climate reanalysis data and
              actionable hydrological intelligence.
            </p>
          </div>

          {/* Two-column layout */}
          <div className="mt-16 grid items-center gap-12 lg:grid-cols-2">
            {/* Left: visual */}
            <div className="overflow-hidden rounded-xl border border-slate-200 bg-white">
              <div className="aspect-[4/3] flex items-center justify-center bg-gradient-to-br from-slate-50 to-slate-100 p-12">
                <div className="text-center">
                  <div className="mx-auto mb-6 grid grid-cols-3 gap-3">
                    {["#2563EB", "#16A34A", "#EA580C"].map((color, i) => (
                      <div key={i} className="flex flex-col items-center gap-2">
                        <div
                          className="h-16 w-full rounded-lg"
                          style={{ backgroundColor: `${color}18`, border: `1px solid ${color}30` }}
                        />
                        <div
                          className="h-1 w-8 rounded-full"
                          style={{ backgroundColor: color }}
                        />
                      </div>
                    ))}
                  </div>
                  <p className="text-xs font-medium text-slate-400">
                    Multi-layer analysis visualization
                  </p>
                </div>
              </div>
            </div>

            {/* Right: content list */}
            <div className="space-y-8">
              {[
                {
                  step: "01",
                  title: "Ingest satellite data",
                  description: "Automated pipelines pull ERA5-Land reanalysis data from Copernicus Climate Data Store, covering rainfall, soil moisture, and surface runoff.",
                },
                {
                  step: "02",
                  title: "Process & store",
                  description: "Raster data is stored under the local storage tree with SQLite metadata indexing, enabling fast spatial queries on a single machine.",
                },
                {
                  step: "03",
                  title: "Visualize & analyze",
                  description: "The Studio provides an interactive map with point-click analytics, time series charts, statistical summaries, and data export capabilities.",
                },
              ].map((item) => (
                <div key={item.step} className="flex gap-5">
                  <span className="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-lg bg-blue-50 text-sm font-bold text-blue-600">
                    {item.step}
                  </span>
                  <div>
                    <h3 className="text-base font-semibold text-slate-900">
                      {item.title}
                    </h3>
                    <p className="mt-1.5 text-sm leading-relaxed text-slate-600">
                      {item.description}
                    </p>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>

      {/* ─── Bottom CTA ─── */}
      <section className="bg-[#0A0E27] py-24 sm:py-32">
        <div className="mx-auto max-w-3xl px-6 text-center">
          <h2 className="text-3xl font-bold tracking-tight text-white sm:text-4xl">
            Start analyzing hydrological data today
          </h2>
          <p className="mx-auto mt-4 max-w-xl text-lg text-slate-400">
            No setup required. Launch the Studio and explore rainfall, soil moisture,
            and runoff data across India — instantly.
          </p>
          <div className="mt-10 flex flex-col items-center gap-4 sm:flex-row sm:justify-center">
            <Link
              to="/studio"
              className="inline-flex items-center gap-2 rounded-lg bg-blue-600 px-6 py-3 text-sm font-semibold text-white transition-colors hover:bg-blue-500"
            >
              Launch Studio
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
                <path d="M6 3l5 5-5 5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </Link>
            <a
              href="https://github.com"
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-2 rounded-lg border border-white/15 bg-white/5 px-6 py-3 text-sm font-semibold text-slate-300 transition-colors hover:bg-white/10 hover:text-white"
            >
              View on GitHub
            </a>
          </div>
        </div>
      </section>

      <Footer />
    </div>
  );
}
