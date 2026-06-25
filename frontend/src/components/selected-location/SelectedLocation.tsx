import { useEffect, useState } from "react";
import { useAppStore, Variable } from "../../stores/useAppStore";
import { motion } from "framer-motion";
import { getDistrictStatistics } from "../../api/boundaries";

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
      className="flex h-9 w-9 items-center justify-center rounded-[12px] transition-all duration-200 ease-out"
      style={{ backgroundColor: color ? `${color}14` : "rgba(15,23,42,0.04)" }}
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
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      whileHover={{ y: -2, boxShadow: "0 8px 24px rgba(15,23,42,0.08)" }}
      className="rounded-[16px] border border-slate-900/6 bg-slate-50/60 p-4 transition-all duration-180 ease-out"
    >
      <div className="flex flex-col gap-2">
        <div className="flex items-center gap-3">
          <IconContainer color={color}>
            <span
              className="material-symbols-rounded"
              style={{ fontSize: 24, color }}
            >
              {icon}
            </span>
          </IconContainer>
          <span className="text-xs font-semibold text-slate-500 uppercase tracking-[0.14em]">
            {label}
          </span>
        </div>
        <div className="flex items-baseline gap-1.5">
          <span
            className="text-2xl font-semibold tracking-tight"
            style={{ color }}
          >
            {value.toFixed(6)}
          </span>
          <span className="text-sm text-slate-500">{unit}</span>
        </div>
      </div>
    </motion.div>
  );
}

function SelectedLocation() {
  const selectedStateId = useAppStore((state) => state.selectedStateId);
  const selectedDistrictId = useAppStore((state) => state.selectedDistrictId);
  const states = useAppStore((state) => state.states);
  const districts = useAppStore((state) => state.districts);
  const rightSidebarOpen = useAppStore((state) => state.rightSidebarOpen);
  const setRightSidebarOpen = useAppStore((state) => state.setRightSidebarOpen);
  const [stats, setStats] = useState<Record<Variable, { mean: number; min: number; max: number }> | null>(null);
  const [loading, setLoading] = useState(false);

  const selectedState = states.find((s) => s.id === selectedStateId);
  const selectedDistrict = districts.find((d) => d.id === selectedDistrictId);

  useEffect(() => {
    if (!selectedDistrictId) {
      setStats(null);
      return;
    }

    const districtId = selectedDistrictId;
    let cancelled = false;
    setLoading(true);

    async function fetchAllStats() {
      try {
        const [precipStats, soilStats, runoffStats] = await Promise.all([
          getDistrictStatistics(districtId, 2024, 1, "precipitation"),
          getDistrictStatistics(districtId, 2024, 1, "soil_moisture"),
          getDistrictStatistics(districtId, 2024, 1, "surface_runoff"),
        ]);
        if (cancelled) return;
        setStats({
          precipitation: { mean: precipStats.mean, min: precipStats.min, max: precipStats.max },
          soil_moisture: { mean: soilStats.mean, min: soilStats.min, max: soilStats.max },
          surface_runoff: { mean: runoffStats.mean, min: runoffStats.min, max: runoffStats.max },
        });
      } catch (error) {
        console.error("Failed to fetch statistics:", error);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    fetchAllStats();

    return () => {
      cancelled = true;
    };
  }, [selectedDistrictId]);

  if (!rightSidebarOpen) {
    return (
      <motion.button
        initial={false}
        animate={{ scale: 1 }}
        whileHover={{ scale: 1.05, y: -2 }}
        whileTap={{ scale: 0.98 }}
        type="button"
        onClick={() => setRightSidebarOpen(true)}
        className="mt-0 flex h-12 w-12 items-center justify-center rounded-[20px] border border-slate-900/6 bg-white/92 shadow-[0_12px_40px_rgba(15,23,42,0.08)] backdrop-blur-[22px] transition-all duration-180 ease-out hover:shadow-[0_16px_50px_rgba(15,23,42,0.12)]"
      >
        <span className="material-symbols-rounded text-slate-600" style={{ fontSize: 20 }}>
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
      whileHover={{ y: -1, boxShadow: "0 16px 50px rgba(15,23,42,0.12)" }}
      className="mt-0 w-full rounded-[20px] border border-slate-900/6 bg-white/92 px-5 py-5 shadow-[0_12px_40px_rgba(15,23,42,0.08)] backdrop-blur-[22px] transition-all duration-180 ease-out"
    >
      <div className="flex items-center justify-between mb-4">
        <p className="text-sm font-semibold text-slate-900 tracking-tight">
          Selected Region
        </p>
        <motion.button
          whileHover={{ scale: 1.05, backgroundColor: "rgba(15,23,42,0.04)" }}
          whileTap={{ scale: 0.95 }}
          type="button"
          onClick={() => setRightSidebarOpen(false)}
          className="flex h-8 w-8 items-center justify-center rounded-full transition-colors duration-200 ease-out"
        >
          <span className="material-symbols-rounded text-slate-500" style={{ fontSize: 18 }}>
            close
          </span>
        </motion.button>
      </div>

      <div className="flex gap-2 rounded-[14px] bg-slate-50/80 px-3 py-2.5 mb-4">
        <div className="flex-1">
          <span className="text-[10px] font-semibold text-slate-400 uppercase tracking-[0.16em]">
            State
          </span>
          <p className="text-sm font-medium text-slate-800">
            {selectedState?.name || "-"}
          </p>
        </div>
        <div className="w-px bg-slate-200" />
        <div className="flex-1">
          <span className="text-[10px] font-semibold text-slate-400 uppercase tracking-[0.16em]">
            District
          </span>
          <p className="text-sm font-medium text-slate-800">
            {selectedDistrict?.name || "-"}
          </p>
        </div>
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-8">
          <div className="h-6 w-6 animate-spin rounded-full border-2 border-slate-200 border-t-blue-600" />
        </div>
      ) : stats ? (
        <div className="grid grid-cols-1 gap-3 mb-4">
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
      ) : (
        <div className="flex items-center justify-center py-8 text-sm text-slate-500">
          No data available
        </div>
      )}

      <div className="flex justify-between items-center text-[11px] text-slate-500">
        <div className="flex items-center gap-1.5">
          <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" />
          <span>Data source: ERA5-Land</span>
        </div>
        <span>Jan 2024</span>
      </div>
    </motion.div>
  );
}

export default SelectedLocation;
