import { useAppStore } from "../../stores/useAppStore";
import { useState } from "react";

function HydraHeader() {
  const timelineDate = useAppStore((state) => state.timelineDate);
  const setTimelineDate = useAppStore((state) => state.setTimelineDate);
  const [settingsHovered, setSettingsHovered] = useState(false);

  return (
    <header className="w-full flex items-center justify-between rounded-[20px] border border-slate-900/6 bg-white/92 px-6 py-3.5 shadow-[0_12px_40px_rgba(15,23,42,0.08)] backdrop-blur-[22px] transition-transform duration-180 hover:-translate-y-0.5">
      <div className="min-w-0 flex items-center gap-4">
        <div>
          <p className="text-lg font-semibold tracking-[-0.02em] text-slate-900">
            HydraAtlas
          </p>
          <p className="text-xs text-slate-500">Hydrology Analytics Platform</p>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <div className="relative">
          <input
            type="search"
            aria-label="Search"
            placeholder="Search datasets, basins, districts..."
            className="w-72 rounded-full border border-slate-900/6 bg-slate-50/70 pl-9 pr-4 py-2.5 text-sm text-slate-700 outline-none transition-all duration-200 ease-out placeholder:text-slate-400 focus:border-blue-500/30 focus:bg-white focus:shadow-[0_0_0_3px_rgba(37,99,235,0.08)]"
          />
          <span className="material-symbols-rounded pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" style={{ fontSize: 20 }}>
            search
          </span>
        </div>

        <div className="relative">
          <input
            type="search"
            aria-label="Coordinate search"
            placeholder="78.96, 22.59"
            className="w-44 rounded-full border border-slate-900/6 bg-slate-50/70 pl-9 pr-4 py-2.5 text-sm text-slate-700 outline-none transition-all duration-200 ease-out placeholder:text-slate-400 focus:border-blue-500/30 focus:bg-white focus:shadow-[0_0_0_3px_rgba(37,99,235,0.08)]"
          />
          <span className="material-symbols-rounded pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" style={{ fontSize: 18 }}>
            pin_drop
          </span>
        </div>

        <label className="flex items-center gap-2 rounded-full border border-slate-900/6 bg-slate-50/70 px-4 py-2.5 text-sm text-slate-600 transition-all duration-200 ease-out hover:bg-white">
          <span className="material-symbols-rounded text-slate-400" style={{ fontSize: 18 }}>
            calendar_today
          </span>
          <input
            type="date"
            aria-label="Date selector"
            value={timelineDate}
            onChange={(event) => setTimelineDate(event.target.value)}
            className="w-32 bg-transparent text-slate-700 outline-none"
          />
        </label>

        <button
          type="button"
          onMouseEnter={() => setSettingsHovered(true)}
          onMouseLeave={() => setSettingsHovered(false)}
          className="flex items-center gap-2 rounded-full border border-slate-900/6 bg-slate-50/70 px-3 py-2.5 text-sm font-medium text-slate-700 transition-all duration-200 ease-out hover:bg-white hover:shadow-[0_0_0_3px_rgba(15,23,42,0.04)] active:scale-[0.98]"
        >
          <span className="material-symbols-rounded text-slate-600" style={{ fontSize: 20 }}>
            settings
          </span>
          {settingsHovered && (
            <span className="text-xs font-medium">Settings</span>
          )}
        </button>
      </div>
    </header>
  );
}

export default HydraHeader;
