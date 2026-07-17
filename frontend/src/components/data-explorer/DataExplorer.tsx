import { useEffect, useMemo, useState } from "react";
import {
  useAppStore,
  monthStringToYearMonth,
  LayerKey,
} from "../../stores/useAppStore";
import { getDatasets, getDistricts, getStates } from "../../api/boundaries";

const MONTH_NAMES = [
  "Jan", "Feb", "Mar", "Apr", "May", "Jun",
  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
];

const LAYER_LABELS: Record<
  LayerKey,
  { name: string; color: string; icon: string; variable: "precipitation" | "soil_moisture" | "surface_runoff" }
> = {
  rainfall: { name: "Rainfall", color: "#2563EB", icon: "rainy", variable: "precipitation" },
  "soil-moisture": { name: "Soil Moisture", color: "#16A34A", icon: "water_drop", variable: "soil_moisture" },
  runoff: { name: "Surface Runoff", color: "#EA580C", icon: "waves", variable: "surface_runoff" },
};

// Shared styling tokens so every field in this panel stays visually
// identical without repeating the same Tailwind string at each call site.
const SELECT_CLASS =
  "w-full h-9 rounded-md border border-slate-200 bg-white px-2.5 text-sm text-slate-800 outline-none transition-colors focus:border-slate-400 disabled:bg-slate-50 disabled:text-slate-400 disabled:cursor-not-allowed";
const FIELD_LABEL_CLASS = "block text-xs text-slate-600 mb-1";
const SECTION_LABEL_CLASS = "text-[11px] font-semibold uppercase tracking-wider text-slate-500";

interface AvailablePeriods {
  years: number[];
  monthsByYear: Record<number, number[]>;
}

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
  const selectedVariable = useAppStore((state) => state.selectedVariable);
  const setStates = useAppStore((state) => state.setStates);
  const setDistricts = useAppStore((state) => state.setDistricts);
  const setSelectedStateId = useAppStore((state) => state.setSelectedStateId);
  const setSelectedDistrictId = useAppStore((state) => state.setSelectedDistrictId);
  const setSelectedVariable = useAppStore((state) => state.setSelectedVariable);
  const setStartMonth = useAppStore((state) => state.setStartMonth);
  const setEndMonth = useAppStore((state) => state.setEndMonth);
  const setAvailableRange = useAppStore((state) => state.setAvailableRange);
  const [startYearDraft, setStartYearDraft] = useState<number | null>(null);
  const [startMonthDraft, setStartMonthDraft] = useState<number | null>(null);
  const [endYearDraft, setEndYearDraft] = useState<number | null>(null);
  const [endMonthDraft, setEndMonthDraft] = useState<number | null>(null);
  const [availablePeriods, setAvailablePeriods] = useState<AvailablePeriods>({
    years: [],
    monthsByYear: {},
  });

  // A period must be chosen before region selection unlocks. This keeps
  // users from picking a district before they've scoped the time range
  // that every downstream fetch (statistics, time-series, raster) needs.
  const periodReady = Boolean(startMonth && endMonth);

  useEffect(() => {
    getStates()
      .then((data) => setStates(data.map((item) => ({ id: item.state_id, name: item.name }))))
      .catch((error) => console.error("Failed to fetch states:", error));
  }, [setStates]);

  // Seeds the month pickers from the dataset's actual available range on
  // first load only; later user edits to Start/End Month are preserved.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const assets = await getDatasets();
        if (cancelled) return;
        if (assets.length === 0) {
          setAvailablePeriods({ years: [], monthsByYear: {} });
          setAvailableRange(null);
          return;
        }
        const monthsByYear = assets.reduce<Record<number, Set<number>>>((acc, asset) => {
          if (!acc[asset.year]) acc[asset.year] = new Set<number>();
          acc[asset.year].add(asset.month);
          return acc;
        }, {});
        const years = Object.keys(monthsByYear)
          .map(Number)
          .sort((a, b) => a - b);
        const normalizedMonthsByYear = Object.fromEntries(
          years.map((year) => [year, Array.from(monthsByYear[year]).sort((a, b) => a - b)]),
        ) as Record<number, number[]>;
        setAvailablePeriods({ years, monthsByYear: normalizedMonthsByYear });
        let minYear = assets[0].year, minMonth = assets[0].month;
        let maxYear = assets[0].year, maxMonth = assets[0].month;
        for (const asset of assets) {
          const key = asset.year * 12 + (asset.month - 1);
          if (key < minYear * 12 + (minMonth - 1)) [minYear, minMonth] = [asset.year, asset.month];
          if (key > maxYear * 12 + (maxMonth - 1)) [maxYear, maxMonth] = [asset.year, asset.month];
        }
        setAvailableRange({ minYear, minMonth, maxYear, maxMonth });
      } catch (error) {
        if (!cancelled) console.error("Failed to fetch dataset range:", error);
      }
    })();
    return () => {
      cancelled = true;
    };
    // Runs once on mount; startMonth/endMonth changes afterward are
    // user-driven and shouldn't re-trigger this seed.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Fetch districts when the selected state changes. Guarded against the
  // classic S1 -> S2 -> S3 race with both an AbortController and an
  // identity check against the store's latest selectedStateId, so a late
  // response can never overwrite a newer selection.
  useEffect(() => {
    if (!selectedStateId) {
      setDistricts([]);
      return;
    }
    const stateId = selectedStateId;
    const ac = new AbortController();
    let cancelled = false;

    getDistricts(stateId, ac.signal)
      .then((data) => {
        if (cancelled || useAppStore.getState().selectedStateId !== stateId) return;
        setDistricts(data.map((item) => ({ id: item.district_id, name: item.name })));
      })
      .catch((error) => {
        if (cancelled) return;
        const aborted =
          (error instanceof DOMException && error.name === "AbortError") ||
          (error instanceof Error && /AbortError/i.test(error.message));
        if (!aborted) console.error("Failed to fetch districts:", error);
      });

    return () => {
      cancelled = true;
      ac.abort();
    };
  }, [selectedStateId, setDistricts]);

  const startYM = useMemo(() => monthStringToYearMonth(startMonth), [startMonth]);
  const endYM = useMemo(() => monthStringToYearMonth(endMonth), [endMonth]);

  const startYear = startYearDraft;
  const startMonthValue = startMonthDraft;
  const endYear = endYearDraft;
  const endMonthValue = endMonthDraft;

  const startMonthOptions = startYear ? availablePeriods.monthsByYear[startYear] ?? [] : [];
  const endMonthOptions = endYear ? availablePeriods.monthsByYear[endYear] ?? [] : [];

  useEffect(() => {
    if (!startMonth) {
      setStartYearDraft(null);
      setStartMonthDraft(null);
    } else if (startYM) {
      setStartYearDraft(startYM.year);
      setStartMonthDraft(startYM.month);
    }
  }, [startMonth, startYM]);

  useEffect(() => {
    if (!endMonth) {
      setEndYearDraft(null);
      setEndMonthDraft(null);
    } else if (endYM) {
      setEndYearDraft(endYM.year);
      setEndMonthDraft(endYM.month);
    }
  }, [endMonth, endYM]);

  // Enforces Start <= End by snapping the other end forward/back when a
  // pick would invert the range.
  const handleStartYearChange = (year: number | null) => {
    if (year === null) {
      setStartYearDraft(null);
      setStartMonthDraft(null);
      setStartMonth("");
      return;
    }
    setStartYearDraft(year);
    const availableMonths = availablePeriods.monthsByYear[year] ?? [];
    const nextMonth = startMonthValue && availableMonths.includes(startMonthValue)
      ? startMonthValue
      : availableMonths[0] ?? null;
    setStartMonthDraft(nextMonth);
    if (nextMonth !== null) {
      setStartMonth(`${year}-${String(nextMonth).padStart(2, "0")}`);
    } else {
      setStartMonth("");
    }
  };

  const handleStartMonthChange = (month: number | null) => {
    if (startYear === null || month === null) {
      setStartMonthDraft(month);
      setStartMonth("");
      return;
    }
    setStartMonthDraft(month);
    const nextStart = `${startYear}-${String(month).padStart(2, "0")}`;
    setStartMonth(nextStart);
    const nextKey = startYear * 12 + month;
    const endKey = endYM ? endYM.year * 12 + endYM.month : null;
    if (endKey !== null && nextKey > endKey) {
      setEndMonth(nextStart);
    }
  };

  const handleEndYearChange = (year: number | null) => {
    if (year === null) {
      setEndYearDraft(null);
      setEndMonthDraft(null);
      setEndMonth("");
      return;
    }
    setEndYearDraft(year);
    const availableMonths = availablePeriods.monthsByYear[year] ?? [];
    const nextMonth = endMonthValue && availableMonths.includes(endMonthValue)
      ? endMonthValue
      : availableMonths[availableMonths.length - 1] ?? null;
    setEndMonthDraft(nextMonth);
    if (nextMonth !== null) {
      setEndMonth(`${year}-${String(nextMonth).padStart(2, "0")}`);
    } else {
      setEndMonth("");
    }
  };

  const handleEndMonthChange = (month: number | null) => {
    if (endYear === null || month === null) {
      setEndMonthDraft(month);
      setEndMonth("");
      return;
    }
    setEndMonthDraft(month);
    const nextEnd = `${endYear}-${String(month).padStart(2, "0")}`;
    setEndMonth(nextEnd);
    const nextKey = endYear * 12 + month;
    const startKey = startYM ? startYM.year * 12 + startYM.month : null;
    if (startKey !== null && nextKey < startKey) {
      setStartMonth(nextEnd);
    }
  };

  if (!sidebarOpen) {
    return (
      <button
        type="button"
        onClick={() => setSidebarOpen(true)}
        className="flex h-9 items-center gap-2 rounded-md border border-slate-200 bg-white px-3 text-sm font-medium text-slate-700 transition-colors hover:bg-slate-50"
        aria-label="Open data explorer"
      >
        <span className="material-symbols-rounded text-slate-600" style={{ fontSize: 18 }}>menu</span>
        Data Explorer
      </button>
    );
  }

  return (
    <div className="w-[300px] rounded-md border border-slate-200 bg-white shadow-sm">
      <div className="flex items-center justify-between px-4 py-3 border-b border-slate-200">
        <p className="text-sm font-semibold text-slate-900">Data Explorer</p>
        <button
          type="button"
          onClick={() => setSidebarOpen(false)}
          className="flex h-6 w-6 items-center justify-center rounded-md text-slate-500 transition-colors hover:bg-slate-100"
          aria-label="Close data explorer"
        >
          <span className="material-symbols-rounded" style={{ fontSize: 16 }}>close</span>
        </button>
      </div>

      <div className="px-4 py-4 space-y-5">
        {/* PERIOD — chosen first; everything else scopes to this range */}
        <div className="space-y-2">
          <p className={SECTION_LABEL_CLASS}>Period</p>
          <div className="space-y-2">
            <PeriodRow
              idPrefix="start"
              label="Start month"
              year={startYear}
              month={startMonthValue}
              yearOptions={availablePeriods.years}
              monthOptions={startMonthOptions}
              onYearChange={handleStartYearChange}
              onMonthChange={handleStartMonthChange}
            />
            <PeriodRow
              idPrefix="end"
              label="End month"
              year={endYear}
              month={endMonthValue}
              yearOptions={availablePeriods.years}
              monthOptions={endMonthOptions}
              onYearChange={handleEndYearChange}
              onMonthChange={handleEndMonthChange}
            />
          </div>
        </div>

        {/* REGION — locked until a period is set */}
        <div className="space-y-2">
          <p className={SECTION_LABEL_CLASS}>Region</p>
          {!periodReady && (
            <p className="text-xs text-slate-400">Select a period above to choose a region.</p>
          )}
          <div className="space-y-2">
            <div>
              <label htmlFor="state-select" className={FIELD_LABEL_CLASS}>State</label>
              <select
                id="state-select"
                value={selectedStateId || ""}
                onChange={(e) => setSelectedStateId(e.target.value || null)}
                disabled={!periodReady}
                className={SELECT_CLASS}
              >
                <option value="">Select a state</option>
                {states.map((state) => (
                  <option key={state.id} value={state.id}>{state.name}</option>
                ))}
              </select>
            </div>
            <div>
              <label htmlFor="district-select" className={FIELD_LABEL_CLASS}>District</label>
              <select
                id="district-select"
                value={selectedDistrictId || ""}
                onChange={(e) => setSelectedDistrictId(e.target.value || null)}
                disabled={!periodReady || !selectedStateId}
                className={SELECT_CLASS}
              >
                <option value="">Select a district</option>
                {districts.map((district) => (
                  <option key={district.id} value={district.id}>{district.name}</option>
                ))}
              </select>
            </div>
          </div>
        </div>

        {/* DATASET */}
        <div className="space-y-2">
          <p className={SECTION_LABEL_CLASS}>Dataset</p>
          <div className="flex items-center gap-2 h-9 px-2.5 rounded-md border border-slate-200 bg-slate-50">
            <span className="material-symbols-rounded text-slate-500" style={{ fontSize: 18 }}>public</span>
            <span className="text-sm font-medium text-slate-700">ERA5-Land</span>
          </div>
        </div>

        {/* LAYERS */}
        <div className="space-y-2">
          <p className={SECTION_LABEL_CLASS}>Layers</p>
          <div className="space-y-1">
            {(Object.keys(LAYER_LABELS) as LayerKey[]).map((layerKey) => {
              const data = LAYER_LABELS[layerKey];
              const enabled = layers[layerKey].enabled;
              const active = selectedVariable === data.variable;
              return (
                <div
                  key={layerKey}
                  role="button"
                  tabIndex={0}
                  onClick={() => setSelectedVariable(data.variable)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      setSelectedVariable(data.variable);
                    }
                  }}
                  className={`flex items-center justify-between h-9 px-2.5 rounded-md border bg-white transition-colors ${
                    active ? "border-slate-900 ring-1 ring-slate-300" : "border-slate-200"
                  }`}
                  aria-pressed={active}
                >
                  <div className="flex items-center gap-2">
                    <span
                      className="material-symbols-rounded"
                      style={{ fontSize: 18, color: data.color, opacity: enabled ? 1 : 0.4 }}
                    >
                      {data.icon}
                    </span>
                    <span className={`text-sm font-medium ${enabled ? "text-slate-700" : "text-slate-400"}`}>
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
  monthOptions,
  onYearChange,
  onMonthChange,
}: {
  idPrefix: string;
  label: string;
  year: number | null;
  month: number | null;
  yearOptions: number[];
  monthOptions: number[];
  onYearChange: (year: number | null) => void;
  onMonthChange: (month: number | null) => void;
}) {
  return (
    <div>
      <label className={FIELD_LABEL_CLASS}>{label}</label>
      <div className="flex gap-1.5">
        <select
          id={`${idPrefix}-year`}
          aria-label={`${label} year`}
          value={year ?? ""}
          onChange={(e) => onYearChange(e.target.value ? Number(e.target.value) : null)}
          className={`flex-1 ${SELECT_CLASS} tabular-nums`}
        >
          <option value="">Year</option>
          {yearOptions.map((y) => (
            <option key={y} value={y}>{y}</option>
          ))}
        </select>
        <select
          id={`${idPrefix}-month`}
          aria-label={`${label} month`}
          value={month ?? ""}
          onChange={(e) => onMonthChange(e.target.value ? Number(e.target.value) : null)}
          disabled={year === null}
          className={`flex-[1.2] ${SELECT_CLASS}`}
        >
          <option value="">Month</option>
          {monthOptions.map((monthNumber) => (
            <option key={monthNumber} value={monthNumber}>{MONTH_NAMES[monthNumber - 1]}</option>
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
      onClick={(e) => {
        e.stopPropagation();
        onChange();
      }}
      className={`relative h-5 w-9 rounded-full transition-colors ${checked ? "bg-blue-600" : "bg-slate-200"}`}
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
