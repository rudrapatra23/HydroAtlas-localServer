import { useEffect, useMemo, useState, useRef } from "react";
import { Link } from "react-router-dom";
import { ChevronDown, Check } from "lucide-react";
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

type LayerLabel = {
  name: string;
  color: string;
  icon: string;
  variable: "precipitation" | "soil_moisture" | "surface_runoff";
};

const LAYER_LABELS: Record<LayerKey, LayerLabel> = {
  rainfall: { name: "Rainfall", color: "#0284C7", icon: "rainy", variable: "precipitation" },
  "soil-moisture": { name: "Soil Moisture", color: "#15803D", icon: "water_drop", variable: "soil_moisture" },
  runoff: { name: "Surface Runoff", color: "#C2410C", icon: "waves", variable: "surface_runoff" },
};

const FIELD_LABEL_CLASS = "mb-1 block text-[11px] font-semibold text-slate-500";
const SECTION_LABEL_CLASS = "text-[10px] font-medium uppercase tracking-[0.14em] text-cyan-700/70";

interface AvailablePeriods {
  years: number[];
  monthsByYear: Record<number, number[]>;
}

// Reusable Custom Dropdown Component for beautiful options UI
interface CustomDropdownProps<T> {
  id?: string;
  label: string;
  value: T | null;
  options: { value: T; label: string }[];
  placeholder: string;
  disabled?: boolean;
  onChange: (value: T | null) => void;
  className?: string;
}

function CustomDropdown<T extends string | number>({
  value,
  options,
  placeholder,
  disabled = false,
  onChange,
  className = "",
}: CustomDropdownProps<T>) {
  const [isOpen, setIsOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setIsOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  const selectedOption = options.find((opt) => opt.value === value);

  return (
    <div ref={containerRef} className={`relative w-full ${className}`}>
      <button
        type="button"
        disabled={disabled}
        onClick={() => setIsOpen(!isOpen)}
        className="flex h-9 w-full items-center justify-between rounded-lg border border-slate-200 bg-slate-50/70 px-2.5 pr-8 text-left text-sm font-normal text-slate-700 outline-none transition focus:border-cyan-500 focus:bg-white focus:ring-2 focus:ring-cyan-100 disabled:bg-slate-50 disabled:text-slate-400 disabled:cursor-not-allowed"
      >
        <span className={selectedOption ? "text-slate-800" : "text-slate-400"}>
          {selectedOption ? selectedOption.label : placeholder}
        </span>
        <ChevronDown
          className={`absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-400 transition-transform duration-200 ${isOpen ? "rotate-180" : ""}`}
          size={14}
        />
      </button>

      {isOpen && !disabled && (
        <div className="absolute z-50 mt-1 max-h-60 w-full overflow-auto rounded-lg border border-slate-150 bg-white p-1 shadow-xl shadow-slate-900/5 animate-in fade-in slide-in-from-top-1 duration-150">
          <button
            type="button"
            onClick={() => {
              onChange(null);
              setIsOpen(false);
            }}
            className="flex w-full items-center justify-between rounded-md px-2 py-1.5 text-left text-sm text-slate-400 hover:bg-slate-50"
          >
            {placeholder}
          </button>
          {options.map((opt) => {
            const isSelected = opt.value === value;
            return (
              <button
                key={opt.value}
                type="button"
                onClick={() => {
                  onChange(opt.value);
                  setIsOpen(false);
                }}
                className={`flex w-full items-center justify-between rounded-md px-2 py-1.5 text-left text-sm transition ${
                  isSelected
                    ? "bg-cyan-50 font-medium text-cyan-700"
                    : "text-slate-700 hover:bg-slate-50"
                }`}
              >
                <span>{opt.label}</span>
                {isSelected && <Check size={12} className="text-cyan-600" />}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
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
  const rasterViewMode = useAppStore((state) => state.rasterViewMode);
  const setStates = useAppStore((state) => state.setStates);
  const setDistricts = useAppStore((state) => state.setDistricts);
  const setSelectedStateId = useAppStore((state) => state.setSelectedStateId);
  const setSelectedDistrictId = useAppStore((state) => state.setSelectedDistrictId);
  const setSelectedVariable = useAppStore((state) => state.setSelectedVariable);
  const setRasterViewMode = useAppStore((state) => state.setRasterViewMode);
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

  const periodReady = Boolean(startMonth && endMonth);
  const isSingleMonth = periodReady && startMonth === endMonth;

  useEffect(() => {
    getStates()
      .then((data) => setStates(data.map((item) => ({ id: item.state_id, name: item.name }))))
      .catch((error) => console.error("Failed to fetch states:", error));
  }, [setStates]);

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
  }, []);

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

  const formattedStateOptions = useMemo(() => 
    states.map(s => ({ value: s.id, label: s.name })), [states]
  );

  const formattedDistrictOptions = useMemo(() => 
    districts.map(d => ({ value: d.id, label: d.name })), [districts]
  );

  if (!sidebarOpen) {
    return (
      <button
        type="button"
        onClick={() => setSidebarOpen(true)}
        className="m-3 flex h-9 items-center gap-2 rounded-full border border-slate-200 bg-white/95 px-3 text-sm font-semibold text-slate-800 shadow-lg shadow-slate-900/10 backdrop-blur-xl transition hover:bg-white"
        aria-label="Open data explorer"
      >
        <span className="material-symbols-rounded text-slate-600" style={{ fontSize: 18 }}>menu</span>
        Data Explorer
      </button>
    );
  }

  return (
    <div className="flex h-full w-[340px] min-w-[320px] max-w-[380px] flex-col border-r border-slate-200/80 bg-white shadow-2xl shadow-slate-900/10 backdrop-blur-xl">
      {/* Header */}
      <div className="flex items-center justify-between gap-3 border-b border-slate-200/70 bg-gradient-to-r from-cyan-50/80 to-white px-3.5 py-2.5">
        <div>
          <Link
            to="/"
            className="inline-flex items-center gap-1 text-[11px] font-bold text-slate-500 transition hover:text-slate-900"
          >
            <span className="material-symbols-rounded" style={{ fontSize: 14 }}>arrow_back</span>
            Home
          </Link>
          <h2 className="mt-0.5 text-[15px] font-semibold tracking-tight text-slate-950">
            Data Explorer
          </h2>
        </div>
        <button
          type="button"
          onClick={() => setSidebarOpen(false)}
          className="flex h-7 w-7 items-center justify-center rounded-lg text-slate-500 transition hover:bg-white hover:text-slate-900"
          aria-label="Close data explorer"
        >
          <span className="material-symbols-rounded" style={{ fontSize: 16 }}>close</span>
        </button>
      </div>

      {/* Body */}
      <div className="flex flex-1 flex-col justify-start gap-2 overflow-hidden px-2.5 py-2.5">
        <div className="divide-y divide-slate-100 overflow-hidden rounded-2xl border border-slate-200 bg-white">
          {/* Period */}
          <div className="px-3 py-2.5">
            <p className={SECTION_LABEL_CLASS}>Period</p>
            <div className="mt-1.5 space-y-2">
              <PeriodRow
                label="Start month"
                year={startYear}
                month={startMonthValue}
                yearOptions={availablePeriods.years}
                monthOptions={startMonthOptions}
                onYearChange={handleStartYearChange}
                onMonthChange={handleStartMonthChange}
              />
              <PeriodRow
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

          {/* Region */}
          <div className="px-3 py-2.5">
            <div className="flex items-baseline justify-between">
              <p className={SECTION_LABEL_CLASS}>Region</p>
              {!periodReady && (
                <span className="text-[10px] font-medium text-slate-400">Pick a period first</span>
              )}
            </div>
            <div className="mt-1.5 grid grid-cols-2 gap-2">
              <div>
                <label className={FIELD_LABEL_CLASS}>State</label>
                <CustomDropdown
                  label="State"
                  value={selectedStateId}
                  options={formattedStateOptions}
                  placeholder="Select"
                  disabled={!periodReady}
                  onChange={(val) => setSelectedStateId(val)}
                />
              </div>
              <div>
                <label className={FIELD_LABEL_CLASS}>District</label>
                <CustomDropdown
                  label="District"
                  value={selectedDistrictId}
                  options={formattedDistrictOptions}
                  placeholder="Select"
                  disabled={!periodReady || !selectedStateId}
                  onChange={(val) => setSelectedDistrictId(val)}
                />
              </div>
            </div>
          </div>

          {/* Dataset */}
          <div className="px-3 py-2.5">
            <p className={SECTION_LABEL_CLASS}>Dataset</p>
            <div className="mt-1.5 flex h-9 items-center gap-2 rounded-lg bg-cyan-50/70 px-2.5">
              <span className="material-symbols-rounded text-cyan-700" style={{ fontSize: 17 }}>public</span>
              <p className="text-[13px] font-medium text-slate-800">ERA5-Land</p>
            </div>
          </div>

          {/* Layers */}
          <div className="px-3 py-2.5">
            <p className={SECTION_LABEL_CLASS}>Layers</p>
            <div className="mt-1.5 space-y-1">
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
                    className={`flex h-9 items-center justify-between rounded-lg border px-2.5 transition ${
                      active ? "border-cyan-300 bg-cyan-50/70 ring-1 ring-cyan-200" : "border-transparent bg-slate-50/70 hover:bg-slate-100"
                    }`}
                    aria-pressed={active}
                  >
                    <div className="flex items-center gap-2">
                      <span
                        className="material-symbols-rounded"
                        style={{ fontSize: 17, color: data.color, opacity: enabled ? 1 : 0.4 }}
                      >
                        {data.icon}
                      </span>
                      <span className={`text-[13px] font-normal ${enabled ? "text-slate-700" : "text-slate-400"}`}>
                        {data.name}
                      </span>
                    </div>
                    <Toggle checked={enabled} onChange={() => toggleLayer(layerKey)} />
                  </div>
                );
              })}
            </div>
          </div>

          {/* Raster view */}
          <div className="px-3 py-2.5">
            <p className={SECTION_LABEL_CLASS}>Raster view</p>
            <div className="mt-1.5 grid grid-cols-2 gap-1 rounded-lg bg-slate-100 p-1">
              {[
                { mode: "average" as const, label: "Range avg" },
                { mode: "month" as const, label: "1 month" },
              ].map((option) => (
                <button
                  key={option.mode}
                  type="button"
                  onClick={() => setRasterViewMode(option.mode)}
                  disabled={isSingleMonth}
                  className={`rounded-md px-2 py-1.5 text-xs font-medium transition ${
                    rasterViewMode === option.mode
                      ? "bg-white text-cyan-700 shadow-sm"
                      : "text-slate-500 hover:text-slate-800"
                  } ${isSingleMonth ? "opacity-50 cursor-not-allowed" : ""}`}
                >
                  {option.label}
                </button>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function PeriodRow({
  label,
  year,
  month,
  yearOptions,
  monthOptions,
  onYearChange,
  onMonthChange,
}: {
  label: string;
  year: number | null;
  month: number | null;
  yearOptions: number[];
  monthOptions: number[];
  onYearChange: (year: number | null) => void;
  onMonthChange: (month: number | null) => void;
}) {
  const formattedYearOptions = useMemo(() => 
    yearOptions.map(y => ({ value: y, label: String(y) })), [yearOptions]
  );

  const formattedMonthOptions = useMemo(() => 
    monthOptions.map(m => ({ value: m, label: MONTH_NAMES[m - 1] })), [monthOptions]
  );

  return (
    <div>
      <label className={FIELD_LABEL_CLASS}>{label}</label>
      <div className="grid grid-cols-[0.9fr_1.1fr] gap-2">
        <CustomDropdown
          label="Year"
          value={year}
          options={formattedYearOptions}
          placeholder="Year"
          onChange={onYearChange}
          className="tabular-nums"
        />
        <CustomDropdown
          label="Month"
          value={month}
          options={formattedMonthOptions}
          placeholder="Month"
          disabled={year === null}
          onChange={onMonthChange}
        />
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
      className={`relative h-5 w-9 rounded-full transition-colors ${checked ? "bg-cyan-500" : "bg-slate-200"}`}
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