import { useAppStore } from "../../stores/useAppStore";
import { useState } from "react";

function HydraHeader() {
  // Date pickers were moved into DataExplorer to keep this header
  // focused on identity + global actions. The store fields are
  // unchanged; nothing here reads them anymore.
  const [settingsHovered, setSettingsHovered] = useState(false);

  return (
    <header className="w-full flex items-center justify-between rounded-md border border-slate-200 bg-white px-5 py-3 transition-colors">
      <div className="min-w-0 flex items-center gap-3">
        <div className="flex h-8 w-8 items-center justify-center rounded-md bg-slate-900 text-white text-sm font-semibold">
          H
        </div>
        <div>
          <p className="text-base font-semibold tracking-tight text-slate-900">
            HydraAtlas
          </p>
          <p className="text-xs text-slate-500">Hydrology Analytics Platform</p>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <div className="relative">
          <input
            type="search"
            aria-label="Search"
            placeholder="Search datasets, basins, districts..."
            className="w-72 rounded-md border border-slate-200 bg-slate-50 pl-9 pr-3 py-2 text-sm text-slate-700 outline-none transition-colors placeholder:text-slate-400 focus:border-slate-400 focus:bg-white"
          />
          <span className="material-symbols-rounded pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" style={{ fontSize: 18 }}>
            search
          </span>
        </div>

        <button
          type="button"
          onMouseEnter={() => setSettingsHovered(true)}
          onMouseLeave={() => setSettingsHovered(false)}
          className="flex items-center gap-2 rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-sm font-medium text-slate-700 transition-colors hover:bg-white"
        >
          <span className="material-symbols-rounded text-slate-600" style={{ fontSize: 18 }}>
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
