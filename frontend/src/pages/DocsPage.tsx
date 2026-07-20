import { Link } from "react-router-dom";
import Navbar from "../components/landing/Navbar";
import Footer from "../components/landing/Footer";

const guideSections = [
  {
    title: "Data Explorer",
    icon: "explore",
    description: "Start by selecting your time period of interest. The Data Explorer locks region selection until a period is set to ensure accurate climate calculations. Once a period is set, select a State and District to zoom directly to your area of interest. You can seamlessly toggle between Rainfall, Soil Moisture, and Surface Runoff layers.",
  },
  {
    title: "Map Interactions",
    icon: "map",
    description: "The map interface provides immediate visual context. When you hover over regions, the map highlights boundaries. Clicking a district boundary directly updates the Selected Region panel and the bottom analysis charts, bypassing the need to use dropdowns.",
  },
  {
    title: "Selected Region Panel",
    icon: "analytics",
    description: "Located on the right side of the workspace, this panel gives you an instant snapshot of the climate anomalies for your chosen region. It compares current values against historical averages to highlight extreme conditions at a glance.",
  },
  {
    title: "Time-Series Analysis",
    icon: "monitoring",
    description: "The bottom panel contains interactive charts mapping out the complete temporal sequence of your selected data over the chosen period. You can scrub through the months and track exact climatic shifts over time.",
  },
  {
    title: "Rainfall Trends",
    icon: "trending_up",
    description: "Analyze the long-term trends of rainfall anomalies over your selected region to identify changing seasonal patterns and extended dry or wet spells.",
  },
  {
    title: "Statistical Summary",
    icon: "query_stats",
    description: "Access a detailed statistical breakdown of the selected area, including min, max, average anomalies, and standard deviation over the given time horizon.",
  },
  {
    title: "Data Export & Downloads",
    icon: "download",
    description: "Easily export your queried spatial and time-series data as structured formats (like CSV or GeoJSON) to integrate seamlessly into your external reports or local workflows.",
  },
];

export default function DocsPage() {
  return (
    <div className="min-h-screen bg-slate-950 text-white">
      <Navbar />
      
      <main className="pt-32 pb-24 sm:pt-40">
        <div className="mx-auto max-w-4xl px-6">
          <div className="mb-16 max-w-2xl">
            <p className="text-sm font-bold uppercase tracking-[0.2em] text-cyan-300">
              Documentation
            </p>
            <h1 className="mt-4 text-4xl font-black tracking-tight text-white sm:text-6xl">
              User Guide
            </h1>
            <p className="mt-6 text-lg leading-relaxed text-slate-300">
              Welcome to the HydraAtlas User Guide. This manual walks you through the core workflows of the Studio, helping you track and analyze hydrology signals efficiently.
            </p>
          </div>

          <div className="grid gap-6 sm:grid-cols-2">
            {guideSections.map((section, idx) => (
              <div 
                key={section.title}
                className="rounded-[2rem] border border-white/10 bg-white/5 p-8 backdrop-blur transition hover:bg-white/10"
              >
                <div className="mb-6 flex h-12 w-12 items-center justify-center rounded-2xl bg-slate-900 text-cyan-300 shadow-inner">
                  <span className="material-symbols-rounded">{section.icon}</span>
                </div>
                <h2 className="text-xl font-bold text-white">
                  <span className="mr-2 text-slate-500">0{idx + 1}.</span>
                  {section.title}
                </h2>
                <p className="mt-4 text-sm leading-7 text-slate-400">
                  {section.description}
                </p>
              </div>
            ))}
          </div>

          <div className="mt-16 flex items-center gap-4 border-t border-white/10 pt-10">
            <Link
              to="/studio"
              className="inline-flex items-center justify-center rounded-xl bg-cyan-400 px-6 py-3 text-sm font-bold text-slate-950 shadow-lg shadow-cyan-500/20 transition hover:bg-cyan-300"
            >
              Launch Studio
            </Link>
            <Link
              to="/"
              className="inline-flex items-center justify-center rounded-xl border border-white/15 bg-white/5 px-6 py-3 text-sm font-bold text-white transition hover:bg-white/10"
            >
              Back to Home
            </Link>
          </div>
        </div>
      </main>

      <Footer />
    </div>
  );
}
