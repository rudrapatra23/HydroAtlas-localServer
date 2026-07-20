import { Link } from "react-router-dom";
import Navbar from "../components/landing/Navbar";
import Footer from "../components/landing/Footer";

const showcaseItems = [
  {
    title: "National Water Policy Planning",
    icon: "account_balance",
    description: "Researchers are leveraging the hydrology maps to advise policy-making and resource allocation across major river basins.",
  },
  {
    title: "Drought Prediction Models",
    icon: "water_drop",
    description: "Using the historical ERA5-Land anomaly data to train predictive models for seasonal drought conditions in agricultural hubs.",
  },
  {
    title: "Flood Risk Assessment",
    icon: "warning",
    description: "Combining high-resolution surface runoff data with elevation maps to identify high-risk zones for flash floods during monsoon seasons.",
  },
  {
    title: "Agriculture & Crop Yield",
    icon: "agriculture",
    description: "Farmers and agronomists analyzing soil moisture trends to optimize irrigation schedules and improve crop yield resilience.",
  }
];

export default function ShowcasePage() {
  return (
    <div className="min-h-screen bg-slate-950 text-white">
      <Navbar />
      
      <main className="pt-32 pb-24 sm:pt-40">
        <div className="mx-auto max-w-4xl px-6">
          <div className="mb-16 max-w-2xl">
            <p className="text-sm font-bold uppercase tracking-[0.2em] text-cyan-300">
              Showcase
            </p>
            <h1 className="mt-4 text-4xl font-black tracking-tight text-white sm:text-6xl">
              Built with HydraAtlas
            </h1>
            <p className="mt-6 text-lg leading-relaxed text-slate-300">
              Explore projects and research powered by the HydraAtlas platform. Case studies and real-world examples from our community.
            </p>
          </div>

          <div className="grid gap-6 sm:grid-cols-2">
            {showcaseItems.map((item, idx) => (
              <div 
                key={item.title}
                className="rounded-[2rem] border border-white/10 bg-white/5 p-8 backdrop-blur transition hover:bg-white/10"
              >
                <div className="mb-6 flex h-12 w-12 items-center justify-center rounded-2xl bg-slate-900 text-cyan-300 shadow-inner">
                  <span className="material-symbols-rounded">{item.icon}</span>
                </div>
                <h2 className="text-xl font-bold text-white">
                  <span className="mr-2 text-slate-500">0{idx + 1}.</span>
                  {item.title}
                </h2>
                <p className="mt-4 text-sm leading-7 text-slate-400">
                  {item.description}
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
