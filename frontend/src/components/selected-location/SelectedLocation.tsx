import { useEffect, useState } from "react";
import {
  useAppStore,
  Variable,
  monthStringToYearMonth,
} from "../../stores/useAppStore";
import { motion } from "framer-motion";
import { getDistrictRangeStatistics } from "../../api/boundaries";

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

function SelectedLocation() {
  const selectedStateId = useAppStore((state) => state.selectedStateId);
  const selectedDistrictId = useAppStore((state) => state.selectedDistrictId);
  const states = useAppStore((state) => state.states);
  const districts = useAppStore((state) => state.districts);
  const rightSidebarOpen = useAppStore((state) => state.rightSidebarOpen);
  const setRightSidebarOpen = useAppStore((state) => state.setRightSidebarOpen);
  const startMonth = useAppStore((state) => state.startMonth);
  const endMonth = useAppStore((state) => state.endMonth);
  const [stats, setStats] = useState<Record<Variable, { mean: number; min: number; max: number }> | null>(null);
  const [loading, setLoading] = useState(false);
  const [noDatasetForPeriod, setNoDatasetForPeriod] = useState(false);
  const [monthsProcessed, setMonthsProcessed] = useState(0);

  const selectedState = states.find((s) => s.id === selectedStateId);
  const selectedDistrict = districts.find((d) => d.id === selectedDistrictId);

  // Every change to the selected Start Month or End Month — or to the
  // selected district — must immediately re-run the analysis request.
  useEffect(() => {
    if (!selectedDistrictId) {
      setStats(null);
      setNoDatasetForPeriod(false);
      setMonthsProcessed(0);
      return;
    }

    const districtId = selectedDistrictId;
    const start = monthStringToYearMonth(startMonth);
    const end = monthStringToYearMonth(endMonth);
    if (!start || !end) {
      setStats(null);
      setLoading(false);
      return;
    }
    if (start.year * 12 + start.month > end.year * 12 + end.month) {
      setStats(null);
      setNoDatasetForPeriod(false);
      setLoading(false);
      return;
    }

    let cancelled = false;
    setLoading(true);
    setNoDatasetForPeriod(false);

    async function fetchAllStats() {
      try {
        const [precipStats, soilStats, runoffStats] = await Promise.all([
          getDistrictRangeStatistics(districtId, {
            start_year: start!.year,
            start_month: start!.month,
            end_year: end!.year,
            end_month: end!.month,
            variable: "precipitation",
          }),
          getDistrictRangeStatistics(districtId, {
            start_year: start!.year,
            start_month: start!.month,
            end_year: end!.year,
            end_month: end!.month,
            variable: "soil_moisture",
          }),
          getDistrictRangeStatistics(districtId, {
            start_year: start!.year,
            start_month: start!.month,
            end_year: end!.year,
            end_month: end!.month,
            variable: "surface_runoff",
          }),
        ]);
        if (cancelled) return;
        setStats({
          precipitation: { mean: precipStats.mean, min: precipStats.min, max: precipStats.max },
          soil_moisture: { mean: soilStats.mean, min: soilStats.min, max: soilStats.max },
          surface_runoff: { mean: runoffStats.mean, min: runoffStats.min, max: runoffStats.max },
        });
        setMonthsProcessed(precipStats.months_processed);
      } catch (error) {
        if (cancelled) return;
        const message = error instanceof Error ? error.message : String(error);
        if (/404/.test(message)) {
          setStats(null);
          setMonthsProcessed(0);
          setNoDatasetForPeriod(true);
        } else {
          console.error("Failed to fetch statistics:", error);
          setStats(null);
          setMonthsProcessed(0);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    fetchAllStats();

    return () => {
      cancelled = true;
    };
  }, [selectedDistrictId, startMonth, endMonth]);

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

      {loading ? (
        <div className="flex items-center justify-center py-8">
          <div className="h-5 w-5 animate-spin rounded-full border-2 border-slate-200 border-t-slate-700" />
        </div>
      ) : stats ? (
        <div className="grid grid-cols-1 gap-2 mb-3">
          {KPI_CONFIGS.map((kpi) => (
            <KpiCard
              key={kpi.variable}
              icon={kpi.icon}
              label={kpi.label}
              value={stats[kpi.variable].mean}
              unit={kpi.unit}
              color={kpi.color}
            />
          ))}
        </div>
      ) : noDatasetForPeriod ? (
        <div className="flex items-center justify-center py-8 text-sm text-slate-500">
          No climate data available for the selected period.
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
          {monthsProcessed > 0 ? ` · ${monthsProcessed}mo` : ""}
        </span>
      </div>
    </motion.div>
  );
}

export default SelectedLocation;
