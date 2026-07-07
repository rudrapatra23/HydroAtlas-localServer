import { useMemo } from "react";
import {
  useAppStore,
  Variable,
} from "../../stores/useAppStore";
import { motion } from "framer-motion";
import { useDistrictData } from "../../hooks/useDistrictData";
import { deriveKpis } from "../../stores/districtDataStore";

interface KpiConfig {
  icon: string;
  label: string;
  variable: Variable;
  unit: string;
  color: string;
}

const KPI_CONFIGS: KpiConfig[] = [
  { icon: "rainy", label: "Precipitation", variable: "precipitation", unit: "m", color: "#2563EB" },
  { icon: "water_drop", label: "Soil Moisture", variable: "soil_moisture", unit: "m³/m³", color: "#16A34A" },
  { icon: "waves", label: "Surface Runoff", variable: "surface_runoff", unit: "m", color: "#EA580C" },
];

/**
 * Canonical variable set the right-panel always shows. Listed explicitly
 * (instead of being derived from layer toggles) because the right panel
 * is the user's single source of truth for "what climate variables exist
 * here" regardless of which chart tabs are visible below.
 */
const PANEL_VARIABLES: readonly Variable[] = [
  "precipitation",
  "soil_moisture",
  "surface_runoff",
];

function IconContainer({
  children,
  color,
}: {
  children: React.ReactNode;
  color?: string;
}) {
  return (
    <div
      className="flex h-8 w-8 items-center justify-center rounded-md"
      style={{ backgroundColor: color ? `${color}1A` : "#F1F5F9" }}
    >
      {children}
    </div>
  );
}

function KpiCard({
  icon,
  label,
  value,
  unit,
  color,
}: {
  icon: string;
  label: string;
  value: number;
  unit: string;
  color: string;
}) {
  return (
    <div className="rounded-md border border-slate-200 bg-slate-50 p-3">
      <div className="flex items-center gap-2.5 mb-2">
        <IconContainer color={color}>
          <span
            className="material-symbols-rounded"
            style={{ fontSize: 20, color }}
          >
            {icon}
          </span>
        </IconContainer>
        <span className="text-xs font-medium text-slate-600">
          {label}
        </span>
      </div>
      <div className="flex items-baseline gap-1.5">
        <span
          className="text-xl font-semibold tabular-nums tracking-tight"
          style={{ color }}
        >
          {value.toFixed(6)}
        </span>
        <span className="text-xs text-slate-500">{unit}</span>
      </div>
    </div>
  );
}

/**
 * Small, non-blocking overlay rendered while a new query is in flight.
 * The previous committed KPI data stays visible underneath so the user
 * never sees a destructive blank during district / month / year / range
 * transitions. The badge is truthful: it only signals that a refresh is
 * in progress, not a fake percentage or fabricated backend stage.
 */
function RefreshingBadge({ label = "Updating…" }: { label?: string }) {
  return (
    <div
      className="pointer-events-none absolute right-2 top-2 z-10 flex items-center gap-1.5 rounded-full bg-slate-900/80 px-2.5 py-1 text-[11px] font-medium text-white shadow-sm backdrop-blur-sm"
      role="status"
      aria-live="polite"
    >
      <span
        className="inline-block h-2.5 w-2.5 animate-spin rounded-full border border-white/40 border-t-white"
        aria-hidden="true"
      />
      {label}
    </div>
  );
}

function monthStringToYearMonth(
  monthString: string,
): { year: number; month: number } | null {
  if (!monthString) return null;
  const match = /^(\d{4})-(\d{2})$/.exec(monthString);
  if (!match) return null;
  const year = Number(match[1]);
  const month = Number(match[2]);
  if (!Number.isFinite(year) || !Number.isFinite(month)) return null;
  if (month < 1 || month > 12) return null;
  return { year, month };
}

function SelectedLocation() {
  const selectedStateId = useAppStore((state) => state.selectedStateId);
  const selectedDistrictId = useAppStore((state) => state.selectedDistrictId);
  const states = useAppStore((state) => state.states);
  const districts = useAppStore((state) => state.districts);
  const rightSidebarOpen = useAppStore((state) => state.rightSidebarOpen);
  const setRightSidebarOpen = useAppStore((state) => state.setRightSidebarOpen);
  const startMonth = useAppStore((state) => state.startMonth);
  const endMonth = useAppStore((state) => state.endMonth);

  // Canonical demand-driven data fetch. The hook subscribes to the
  // shared store and ensures exactly one fetch per (district, range,
  // variable-set) key. Both this panel and the BottomPanel consume
  // from the same store entry — no duplicate raster work.
  const data = useDistrictData({
    districtId: selectedDistrictId,
    startMonth: startMonth || null,
    endMonth: endMonth || null,
    variables: PANEL_VARIABLES,
  });

  // Derive KPIs from the canonical time-series response. Equivalence
  // with the previous /districts/{id}/statistics pipeline is proven
  // analytically in `districtDataStore.ts` (mean-of-monthly-means
  // equals min-of-monthly-mins equals max-of-monthly-maxes given the
  // existing _compute_stats_for_geometry semantics).
  const kpisByVariable = useMemo(() => {
    const result: Partial<Record<Variable, { mean: number; min: number; max: number }>> = {};
    for (const v of PANEL_VARIABLES) {
      const kpis = deriveKpis(data.seriesByVariable[v]);
      if (kpis) {
        result[v] = { mean: kpis.mean, min: kpis.min, max: kpis.max };
      }
    }
    return result;
  }, [data.seriesByVariable]);

  const selectedState = states.find((s) => s.id === selectedStateId);
  const selectedDistrict = districts.find((d) => d.id === selectedDistrictId);

  // "Has attempted" reflects whether the canonical entry has reached a
  // terminal state for the current key. We use the presence of either
  // a ready entry or an error/noData entry as the signal.
  const hasAttempted = data.ready || data.noData || data.error !== null;
  const showInitialSpinner = !hasAttempted && data.loading;

  const periodLabel = (() => {
    const start = monthStringToYearMonth(startMonth);
    const end = monthStringToYearMonth(endMonth);
    if (!start || !end) return "";
    const monthNames = [
      "Jan", "Feb", "Mar", "Apr", "May", "Jun",
      "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
    ];
    const startLabel = `${monthNames[start.month - 1]} ${start.year}`;
    const endLabel = `${monthNames[end.month - 1]} ${end.year}`;
    return startLabel === endLabel ? startLabel : `${startLabel} → ${endLabel}`;
  })();

  if (!rightSidebarOpen) {
    return (
      <motion.button
        initial={false}
        animate={{ scale: 1 }}
        whileTap={{ scale: 0.97 }}
        type="button"
        onClick={() => setRightSidebarOpen(true)}
        className="mt-0 flex h-10 w-10 items-center justify-center rounded-md border border-slate-200 bg-white text-slate-700 transition-colors hover:bg-slate-50"
        aria-label="Open selected region"
      >
        <span className="material-symbols-rounded" style={{ fontSize: 18 }}>
          info
        </span>
      </motion.button>
    );
  }

  if (!selectedStateId || !selectedDistrictId) {
    return null;
  }

  // Show KPI cards whenever we have any per-variable KPI derived from
  // the canonical store entry. This keeps the panel useful while a
  // background refresh is running (the previous values stay visible).
  const hasAnyKpi = PANEL_VARIABLES.some((v) => kpisByVariable[v] !== undefined);

  return (
    <motion.div
      initial={{ opacity: 0, x: 20 }}
      animate={{ opacity: 1, x: 0 }}
      className="mt-0 w-full rounded-md border border-slate-200 bg-white px-4 py-4 transition-colors"
    >
      <div className="flex items-center justify-between mb-3">
        <p className="text-sm font-semibold text-slate-900">
          Selected Region
        </p>
        <button
          type="button"
          onClick={() => setRightSidebarOpen(false)}
          className="flex h-7 w-7 items-center justify-center rounded-md text-slate-500 transition-colors hover:bg-slate-100"
          aria-label="Close selected region"
        >
          <span className="material-symbols-rounded" style={{ fontSize: 16 }}>
            close
          </span>
        </button>
      </div>

      <div className="flex gap-3 rounded-md bg-slate-50 border border-slate-200 px-3 py-2.5 mb-3">
        <div className="flex-1">
          <span className="text-xs text-slate-500 block">State</span>
          <p className="text-sm font-medium text-slate-800">
            {selectedState?.name || "-"}
          </p>
        </div>
        <div className="w-px bg-slate-200" />
        <div className="flex-1">
          <span className="text-xs text-slate-500 block">District</span>
          <p className="text-sm font-medium text-slate-800">
            {selectedDistrict?.name || "-"}
          </p>
        </div>
      </div>

      {hasAnyKpi && !data.noData ? (
        <div className="relative">
          <div className="grid grid-cols-1 gap-2 mb-3">
            {KPI_CONFIGS.map((kpi) => {
              const k = kpisByVariable[kpi.variable];
              // Render the card only when this variable's KPI is
              // available in the canonical store. Otherwise leave a
              // placeholder so the layout doesn't pop in/out.
              if (!k) {
                return (
                  <div
                    key={kpi.variable}
                    className="rounded-md border border-slate-200 bg-slate-50 p-3 h-[5.25rem]"
                    aria-hidden="true"
                  />
                );
              }
              return (
                <KpiCard
                  key={kpi.variable}
                  icon={kpi.icon}
                  label={kpi.label}
                  value={k.mean}
                  unit={kpi.unit}
                  color={kpi.color}
                />
              );
            })}
          </div>
          {data.loading && <RefreshingBadge label="Processing new selection…" />}
        </div>
      ) : showInitialSpinner ? (
        <div className="flex items-center justify-center py-8">
          <div className="h-5 w-5 animate-spin rounded-full border-2 border-slate-200 border-t-slate-700" />
        </div>
      ) : data.noData ? (
        <div className="flex items-center justify-center py-8 text-sm text-slate-500">
          No climate data available for the selected period.
        </div>
      ) : data.error ? (
        <div className="flex items-center justify-center py-8 text-sm text-slate-500">
          {data.error}
        </div>
      ) : (
        <div className="flex items-center justify-center py-8 text-sm text-slate-500">
          No data available
        </div>
      )}

      <div className="flex justify-between items-center text-xs text-slate-500 pt-2 border-t border-slate-100">
        <div className="flex items-center gap-1.5">
          <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" />
          <span>ERA5-Land</span>
        </div>
        <span className="tabular-nums">
          {periodLabel}
          {data.monthsProcessed > 0 ? ` · ${data.monthsProcessed}mo` : ""}
        </span>
      </div>
    </motion.div>
  );
}

export default SelectedLocation;
