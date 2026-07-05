import {
  useAppStore,
  yearMonthToMonthString,
  monthStringToYearMonth,
  LayerKey,
} from "../../stores/useAppStore";
import { useEffect, useMemo } from "react";
import { getDatasets, getDistricts, getStates } from "../../api/boundaries";

const MONTH_NAMES = [
  "Jan", "Feb", "Mar", "Apr", "May", "Jun",
  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
];

const LAYER_LABELS: Record<LayerKey, { name: string; color: string; icon: string; variable: "precipitation" | "soil_moisture" | "surface_runoff" }> = {
  rainfall: { name: "Rainfall", color: "#2563EB", icon: "rainy", variable: "precipitation" },
  "soil-moisture": { name: "Soil Moisture", color: "#16A34A", icon: "water_drop", variable: "soil_moisture" },
  runoff: { name: "Surface Runoff", color: "#EA580C", icon: "waves", variable: "surface_runoff" },
};

function DataExplorer() {
  const sidebarOpen = useAppStore((state) => state.leftSidebarOpen);
  const setSidebarOpen = useAppStore((state) => state.setLeftSidebarOpen);
  const layers = useAppStore((state) => state.layers);
  const toggleLayer = useAppStore((state) => state.toggleLayer);
  const states = useAppStore((state) => state.states);
  const districts = useAppStore((state) => state.districts);
  const selectedStateId = useAppStore((state) => state.selectedStateId);
  const selectedDistrictId = useAppStore((state) => state.selectedDistrictId);
  const startMonth = useAppStore((state) => state.startMonth);
  const endMonth = useAppStore((state) => state.endMonth);
  const availableRange = useAppStore((state) => state.availableRange);
  const setStates = useAppStore((state) => state.setStates);
  const setDistricts = useAppStore((state) => state.setDistricts);
  const setSelectedStateId = useAppStore((state) => state.setSelectedStateId);
  const setSelectedDistrictId = useAppStore((state) => state.setSelectedDistrictId);
  const setStartMonth = useAppStore((state) => state.setStartMonth);
  const setEndMonth = useAppStore((state) => state.setEndMonth);
  const setAvailableRange = useAppStore((state) => state.setAvailableRange);

  // Fetch states on mount.
  useEffect(() => {
    async function fetchStates() {
      try {
        const data = await getStates();
        setStates(data.map((item: any) => ({ id: item.state_id, name: item.name })));
      } catch (error) {
        console.error("Failed to fetch states:", error);
      }
    }
    fetchStates();
  }, [setStates]);

  // Fetch the available dataset range on mount and seed the month
  // pickers from it. The range is recomputed from every climate_assets
  // row the backend exposes via GET /datasets, so the UI never assumes
  // a fixed year/month. Only seeds the pickers on the first load —
  // subsequent user edits to Start Month / End Month are preserved.
  useEffect(() => {
    let cancelled = false;
    async function fetchRange() {
      try {
        const assets = await getDatasets();
        if (cancelled) return;
        if (assets.length === 0) {
          setAvailableRange(null);
          return;
        }
        let minYear = assets[0].year;
        let minMonth = assets[0].month;
        let maxYear = assets[0].year;
        let maxMonth = assets[0].month;
        for (const asset of assets) {
          const startKey = asset.year * 12 + (asset.month - 1);
          const minKey = minYear * 12 + (minMonth - 1);
          const maxKey = maxYear * 12 + (maxMonth - 1);
          if (startKey < minKey) {
            minYear = asset.year;
            minMonth = asset.month;
          }
          if (startKey > maxKey) {
            maxYear = asset.year;
            maxMonth = asset.month;
          }
        }
        setAvailableRange({ minYear, minMonth, maxYear, maxMonth });
        if (!startMonth) setStartMonth(yearMonthToMonthString(minYear, minMonth));
        if (!endMonth) setEndMonth(yearMonthToMonthString(maxYear, maxMonth));
      } catch (error) {
        console.error("Failed to fetch dataset range:", error);
      }
    }
    fetchRange();
    return () => {
      cancelled = true;
    };
    // Intentionally only runs once on mount — the range itself is
    // captured in `availableRange` and downstream effects react to
    // startMonth/endMonth changes via the store.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Fetch districts when selected state changes.
  useEffect(() => {
    if (!selectedStateId) {
      setDistricts([]);
      return;
    }
    const stateId = selectedStateId;
    async function fetchDistricts() {
      try {
        const data = await getDistricts(stateId);
        setDistricts(data.map((item: any) => ({ id: item.district_id, name: item.name })));
      } catch (error) {
        console.error("Failed to fetch districts:", error);
      }
    }
    fetchDistricts();
  }, [selectedStateId, setDistricts]);

  // Parse current month strings into {year, month} tuples. Memoised
  // so the year/month select options don't rebuild on every render.
  const startYM = useMemo(() => monthStringToYearMonth(startMonth), [startMonth]);
  const endYM = useMemo(() => monthStringToYearMonth(endMonth), [endMonth]);

  // Build the list of selectable years from availableRange so the user
  // can jump directly from 2026 to 2017 without scrolling. Falls back
  // to the current year ± 2 if the range hasn't loaded yet.
  const yearOptions = useMemo(() => {
    if (!availableRange) {
      const fallback = new Date().getFullYear();
      return [fallback - 1, fallback, fallback + 1];
    }
    const years: number[] = [];
    for (let y = availableRange.minYear; y <= availableRange.maxYear; y++) {
      years.push(y);
    }
    return years;
  }, [availableRange]);

  // Enforce Start Month <= End Month. If the user picks a Start that is
  // after End, we snap End forward to match. If they pick an End that
  // is before Start, we snap Start back. The store still stores
  // ``YYYY-MM`` strings so the API payload is unchanged.
  const handleStartChange = (year: number, month: number) => {
    setStartMonth(yearMonthToMonthString(year, month));
    const endKey = endYM ? endYM.year * 12 + endYM.month : -1;
    const newKey = year * 12 + month;
    if (newKey > endKey) {
      setEndMonth(yearMonthToMonthString(year, month));
    }
  };

  const handleEndChange = (year: number, month: number) => {
    setEndMonth(yearMonthToMonthString(year, month));
    const startKey = startYM ? startYM.year * 12 + startYM.month : Number.MAX_SAFE_INTEGER;
    const newKey = year * 12 + month;
    if (newKey < startKey) {
      setStartMonth(yearMonthToMonthString(year, month));
    }
  };

  // Collapsed state: show only a compact "Data Explorer" toggle.
  if (!sidebarOpen) {
    return (
      <button
        type="button"
        onClick={() => setSidebarOpen(true)}
        className="flex h-9 items-center gap-2 rounded-md border border-slate-200 bg-white px-3 text-sm font-medium text-slate-700 transition-colors hover:bg-slate-50"
        aria-label="Open data explorer"
      >
        <span className="material-symbols-rounded text-slate-600" style={{ fontSize: 18 }}>
          menu
        </span>
        Data Explorer
      </button>
    );
  }

  return (
    <div className="w-[300px] rounded-md border border-slate-200 bg-white">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-slate-200">
        <p className="text-sm font-semibold text-slate-900">Data Explorer</p>
        <button
          type="button"
          onClick={() => setSidebarOpen(false)}
          className="flex h-6 w-6 items-center justify-center rounded-md text-slate-500 transition-colors hover:bg-slate-100"
          aria-label="Close data explorer"
        >
          <span className="material-symbols-rounded" style={{ fontSize: 16 }}>
            close
          </span>
        </button>
      </div>

      <div className="px-4 py-4 space-y-5">
        {/* REGION */}
        <div className="space-y-2">
          <p className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">
            Region
          </p>
          <div className="space-y-2">
            <div>
              <label htmlFor="state-select" className="block text-xs text-slate-600 mb-1">
                State
              </label>
              <select
                id="state-select"
                value={selectedStateId || ""}
                onChange={(e) => setSelectedStateId(e.target.value || null)}
                className="w-full h-9 rounded-md border border-slate-200 bg-white px-2.5 text-sm text-slate-800 outline-none transition-colors focus:border-slate-400"
              >
                <option value="">Select a state</option>
                {states.map((state) => (
                  <option key={state.id} value={state.id}>
                    {state.name}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label htmlFor="district-select" className="block text-xs text-slate-600 mb-1">
                District
              </label>
              <select
                id="district-select"
                value={selectedDistrictId || ""}
                onChange={(e) => setSelectedDistrictId(e.target.value || null)}
                disabled={!selectedStateId}
                className="w-full h-9 rounded-md border border-slate-200 bg-white px-2.5 text-sm text-slate-800 outline-none transition-colors focus:border-slate-400 disabled:bg-slate-50 disabled:text-slate-400"
              >
                <option value="">Select a district</option>
                {districts.map((district) => (
                  <option key={district.id} value={district.id}>
                    {district.name}
                  </option>
                ))}
              </select>
            </div>
          </div>
        </div>

        {/* PERIOD */}
        <div className="space-y-2">
          <p className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">
            Period
          </p>
          <div className="space-y-2">
            <PeriodRow
              idPrefix="start"
              label="Start month"
              year={startYM?.year ?? yearOptions[0]}
              month={startYM?.month ?? 1}
              yearOptions={yearOptions}
              onChange={handleStartChange}
            />
            <PeriodRow
              idPrefix="end"
              label="End month"
              year={endYM?.year ?? yearOptions[yearOptions.length - 1]}
              month={endYM?.month ?? 12}
              yearOptions={yearOptions}
              onChange={handleEndChange}
            />
          </div>
        </div>

        {/* DATASET */}
        <div className="space-y-2">
          <p className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">
            Dataset
          </p>
          <div className="flex items-center gap-2 h-9 px-2.5 rounded-md border border-slate-200 bg-slate-50">
            <span className="material-symbols-rounded text-slate-500" style={{ fontSize: 18 }}>
              public
            </span>
            <span className="text-sm font-medium text-slate-700">ERA5-Land</span>
          </div>
        </div>

        {/* LAYERS */}
        <div className="space-y-2">
          <p className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">
            Layers
          </p>
          <div className="space-y-1">
            {(Object.keys(LAYER_LABELS) as LayerKey[]).map((layerKey) => {
              const data = LAYER_LABELS[layerKey];
              const enabled = layers[layerKey].enabled;
              return (
                <div
                  key={layerKey}
                  className="flex items-center justify-between h-9 px-2.5 rounded-md border border-slate-200 bg-white"
                >
                  <div className="flex items-center gap-2">
                    <span
                      className="material-symbols-rounded"
                      style={{
                        fontSize: 18,
                        color: data.color,
                        opacity: enabled ? 1 : 0.4,
                      }}
                    >
                      {data.icon}
                    </span>
                    <span
                      className={`text-sm font-medium ${
                        enabled ? "text-slate-700" : "text-slate-400"
                      }`}
                    >
                      {data.name}
                    </span>
                  </div>
                  <Toggle checked={enabled} onChange={() => toggleLayer(layerKey)} />
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}

function PeriodRow({
  idPrefix,
  label,
  year,
  month,
  yearOptions,
  onChange,
}: {
  idPrefix: string;
  label: string;
  year: number;
  month: number;
  yearOptions: number[];
  onChange: (year: number, month: number) => void;
}) {
  return (
    <div>
      <label className="block text-xs text-slate-600 mb-1">{label}</label>
      <div className="flex gap-1.5">
        <select
          id={`${idPrefix}-year`}
          aria-label={`${label} year`}
          value={year}
          onChange={(e) => onChange(Number(e.target.value), month)}
          className="flex-1 h-9 rounded-md border border-slate-200 bg-white px-2.5 text-sm text-slate-800 outline-none transition-colors focus:border-slate-400 tabular-nums"
        >
          {yearOptions.map((y) => (
            <option key={y} value={y}>
              {y}
            </option>
          ))}
        </select>
        <select
          id={`${idPrefix}-month`}
          aria-label={`${label} month`}
          value={month}
          onChange={(e) => onChange(year, Number(e.target.value))}
          className="flex-[1.2] h-9 rounded-md border border-slate-200 bg-white px-2.5 text-sm text-slate-800 outline-none transition-colors focus:border-slate-400"
        >
          {MONTH_NAMES.map((name, idx) => (
            <option key={name} value={idx + 1}>
              {name}
            </option>
          ))}
        </select>
      </div>
    </div>
  );
}

function Toggle({ checked, onChange }: { checked: boolean; onChange: () => void }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      onClick={onChange}
      className={`relative h-5 w-9 rounded-full transition-colors ${
        checked ? "bg-blue-600" : "bg-slate-200"
      }`}
    >
      <span
        className={`absolute top-0.5 left-0.5 h-4 w-4 rounded-full bg-white shadow-sm transition-transform ${
          checked ? "translate-x-4" : "translate-x-0"
        }`}
      />
    </button>
  );
}

export default DataExplorer;
