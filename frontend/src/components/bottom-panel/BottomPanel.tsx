import { useAppStore, BottomTab, Variable, LayerKey } from "../../stores/useAppStore";
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  Title,
  Tooltip,
  Legend,
  Filler,
} from "chart.js";
import { Line } from "react-chartjs-2";
import { motion } from "framer-motion";
import { useMemo } from "react";
import { MonthlySeriesPoint } from "../../api/boundaries";
import { useDistrictData } from "../../hooks/useDistrictData";
import type { Variable as CanonicalVariable } from "../../stores/districtDataStore";

ChartJS.register(
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  Title,
  Tooltip,
  Legend,
  Filler
);

const TABS: { id: BottomTab; label: string; icon: string }[] = [
  { id: "time-series", label: "Time Series", icon: "show_chart" },
  { id: "trend", label: "Trend", icon: "trending_up" },
  { id: "statistics", label: "Statistics", icon: "analytics" },
  { id: "export", label: "Export", icon: "download" },
];

interface VariableConfig {
  variable: CanonicalVariable;
  layerKey: LayerKey;
  label: string;
  color: string;
  icon: string;
  unit: string;
}

const VARIABLE_CONFIGS: VariableConfig[] = [
  { variable: "precipitation", layerKey: "rainfall", label: "Rainfall", color: "#2563EB", icon: "rainy", unit: "m" },
  { variable: "soil_moisture", layerKey: "soil-moisture", label: "Soil Moisture", color: "#16A34A", icon: "water_drop", unit: "m³/m³" },
  { variable: "surface_runoff", layerKey: "runoff", label: "Runoff", color: "#EA580C", icon: "waves", unit: "m" },
];

/**
 * Canonical variable set requested by both the right-side
 * `SelectedLocation` panel and this bottom panel. The chart tabs are
 * filtered to the user-enabled subset at render time; the fetch set is
 * always all three so the canonical key is shared.
 */
const CANONICAL_VARIABLES: readonly CanonicalVariable[] = [
  "precipitation",
  "soil_moisture",
  "surface_runoff",
];

/**
 * Variable ↔ LayerKey mapping used to filter chart datasets by which
 * layer toggles are enabled in Data Explorer. Lives at module scope so
 * the dependency array of the memoised enabled-list stays stable.
 */
const VARIABLE_TO_LAYER: Record<CanonicalVariable, LayerKey> = {
  precipitation: "rainfall",
  soil_moisture: "soil-moisture",
  surface_runoff: "runoff",
};

type SeriesByVariable = Record<CanonicalVariable, MonthlySeriesPoint[]>;

const EMPTY_SERIES: SeriesByVariable = {
  precipitation: [],
  soil_moisture: [],
  surface_runoff: [],
};

function formatMonthLabel(point: MonthlySeriesPoint): string {
  const monthNames = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
  ];
  return `${monthNames[point.month - 1]} ${point.year}`;
}

function linearRegression(points: MonthlySeriesPoint[]): { slope: number; intercept: number } | null {
  if (points.length < 2) return null;
  const xs = points.map((_, i) => i);
  const ys = points.map((p) => p.mean);
  const n = xs.length;
  const sumX = xs.reduce((a, b) => a + b, 0);
  const sumY = ys.reduce((a, b) => a + b, 0);
  const sumXY = xs.reduce((acc, x, i) => acc + x * ys[i], 0);
  const sumXX = xs.reduce((acc, x) => acc + x * x, 0);
  const denominator = n * sumXX - sumX * sumX;
  if (denominator === 0) return null;
  const slope = (n * sumXY - sumX * sumY) / denominator;
  const intercept = (sumY - slope * sumX) / n;
  return { slope, intercept };
}

function buildChartOptions(visibleConfigs?: VariableConfig[]) {
  // When `visibleConfigs` is supplied the chart uses two independent
  // y-axes so Soil Moisture (~0.1–0.25 m³/m³) does not flatten
  // Rainfall/Runoff (~0.0001–0.002 m) to a near-zero line. The trend
  // tab calls this with no argument because it only ever plots Rainfall.
  const useDualAxis = Array.isArray(visibleConfigs);
  const hasSoil = useDualAxis
    ? !!visibleConfigs!.some((c) => c.variable === "soil_moisture")
    : false;
  const hasWater = useDualAxis
    ? !!visibleConfigs!.some(
        (c) => c.variable === "precipitation" || c.variable === "surface_runoff",
      )
    : true;

  const scales: Record<string, any> = {
    x: {
      grid: { display: false },
      ticks: {
        maxTicksLimit: 8,
        font: { family: "Inter, sans-serif" },
      },
    },
  };

  if (useDualAxis) {
    scales["y-soil"] = {
      type: "linear",
      position: "left",
      display: hasSoil,
      title: {
        display: hasSoil,
        text: "Soil Moisture (m³/m³)",
        font: { family: "Inter, sans-serif", size: 11 },
        color: "#475569",
      },
      min: 0,
      max: 0.4,
      grid: { color: "rgba(15, 23, 42, 0.06)" },
      ticks: {
        font: { family: "Inter, sans-serif" },
      },
    };
    scales["y-water"] = {
      type: "linear",
      position: "right",
      display: hasWater,
      title: {
        display: hasWater,
        text: "Rainfall / Runoff (m)",
        font: { family: "Inter, sans-serif", size: 11 },
        color: "#475569",
      },
      // Auto-fit to the Rainfall + Runoff range so a layer-toggle
      // combination of only Rainfall, only Runoff, or both still
      // produces a sensible scale.
      grid: { drawOnChartArea: false },
      ticks: {
        font: { family: "Inter, sans-serif" },
      },
    };
  } else {
    scales["y"] = {
      grid: { color: "rgba(15, 23, 42, 0.06)" },
      ticks: {
        font: { family: "Inter, sans-serif" },
      },
    };
  }

  return {
    responsive: true,
    maintainAspectRatio: false,
    interaction: {
      intersect: false,
      mode: "index" as const,
    },
    scales,
    plugins: {
      legend: {
        position: "top" as const,
        align: "end" as const,
        labels: {
          boxWidth: 8,
          boxHeight: 8,
          usePointStyle: true,
          pointStyle: "circle" as const,
          font: {
            family: "Inter, sans-serif",
            size: 12,
            weight: 500 as const,
          },
          padding: 16,
        },
      },
      tooltip: {
        backgroundColor: "#0F172A",
        padding: 10,
        cornerRadius: 6,
        titleFont: {
          family: "Inter, sans-serif",
          weight: 600 as const,
        },
        bodyFont: {
          family: "Inter, sans-serif",
        },
      },
    },
  };
}

function buildSeriesChart(
  series: SeriesByVariable,
  monthsProcessed: number,
  visibleConfigs: VariableConfig[],
) {
  const reference = visibleConfigs.map((c) => series[c.variable])
    .find((points) => points.length > 0) ?? [];
  const labels = reference.map(formatMonthLabel);

  return {
    labels,
    datasets: visibleConfigs.map((config) => {
      const points = series[config.variable];
      // Soil Moisture uses the left axis (m³/m³, 0–0.3); Rainfall and
      // Surface Runoff share the right axis (m, auto-fit). This stops
      // Soil Moisture's larger magnitude from flattening the water-
      // flux series into a near-zero line.
      const yAxisID =
        config.variable === "soil_moisture" ? "y-soil" : "y-water";
      return {
        label: `${config.label} (${config.unit})`,
        data: points.map((p) => p.mean),
        borderColor: config.color,
        backgroundColor: config.variable === "precipitation" ? `${config.color}1A` : "transparent",
        fill: config.variable === "precipitation",
        yAxisID,
        tension: 0.35,
        borderWidth: 2,
        pointRadius: 0,
        pointHoverRadius: 5,
      };
    }),
    _monthsProcessed: monthsProcessed,
  } as any;
}

function buildTrendChart(series: SeriesByVariable, rainfallEnabled: boolean) {
  if (!rainfallEnabled) {
    return {
      labels: [],
      datasets: [],
      _slope: null,
      _intercept: null,
    } as any;
  }

  const rainfall = series.precipitation;
  const labels = rainfall.map(formatMonthLabel);
  const regression = linearRegression(rainfall);
  const trendLine = regression
    ? rainfall.map((_, i) => regression.slope * i + regression.intercept)
    : [];

  return {
    labels,
    datasets: [
      {
        label: "Rainfall (observed)",
        data: rainfall.map((p) => p.mean),
        borderColor: "#2563EB",
        backgroundColor: "transparent",
        tension: 0.35,
        borderWidth: 2,
        pointRadius: 0,
      },
      {
        label: "Rainfall (linear trend)",
        data: trendLine,
        borderColor: "#0F172A",
        backgroundColor: "transparent",
        borderDash: [6, 6],
        tension: 0,
        borderWidth: 2,
        pointRadius: 0,
      },
    ],
    _slope: regression?.slope ?? null,
    _intercept: regression?.intercept ?? null,
  } as any;
}

function computeSeriesStats(points: MonthlySeriesPoint[]) {
  if (points.length === 0) {
    return { min: 0, max: 0, avg: 0 };
  }
  const means = points.map((p) => p.mean);
  const mins = points.map((p) => p.min);
  const maxes = points.map((p) => p.max);
  return {
    min: Math.min(...mins),
    max: Math.max(...maxes),
    avg: means.reduce((a, b) => a + b, 0) / means.length,
  };
}

function TimeSeriesTab({ chart, visibleConfigs }: { chart: any; visibleConfigs: VariableConfig[] }) {
  return (
    <div className="min-h-[16rem] mt-4">
      <Line data={chart} options={buildChartOptions(visibleConfigs)} />
    </div>
  );
}

function TrendTab({ chart }: { chart: any }) {
  return (
    <div className="min-h-[16rem] mt-4">
      <Line data={chart} options={buildChartOptions()} />
    </div>
  );
}

function StatisticsTab({
  series,
  monthsProcessed,
  visibleConfigs,
}: {
  series: SeriesByVariable;
  monthsProcessed: number;
  visibleConfigs: VariableConfig[];
}) {
  const statItems = visibleConfigs.map((config) => ({
    ...config,
    ...computeSeriesStats(series[config.variable]),
  }));

  return (
    <div className="mt-5">
      {monthsProcessed > 0 ? (
        <p className="text-xs text-slate-500 mb-3">
          Aggregated over {monthsProcessed} month{monthsProcessed === 1 ? "" : "s"} of ERA5-Land data.
        </p>
      ) : null}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        {statItems.map((item) => (
          <div
            key={item.label}
            className="rounded-md border border-slate-200 bg-slate-50 p-4"
          >
            <div className="flex items-center gap-2 mb-3">
              <div
                className="flex h-8 w-8 items-center justify-center rounded-md"
                style={{ backgroundColor: `${item.color}1A` }}
              >
                <span
                  className="material-symbols-rounded"
                  style={{ fontSize: 20, color: item.color }}
                >
                  {item.icon}
                </span>
              </div>
              <span className="text-xs font-medium text-slate-600">
                {item.label}
              </span>
            </div>
            <div className="space-y-2">
              <div className="flex justify-between">
                <span className="text-xs text-slate-500">Minimum</span>
                <span className="text-sm font-semibold tabular-nums text-slate-800">
                  {item.min.toFixed(6)} {item.unit}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-xs text-slate-500">Maximum</span>
                <span className="text-sm font-semibold tabular-nums text-slate-800">
                  {item.max.toFixed(6)} {item.unit}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-xs text-slate-500">Average</span>
                <span className="text-sm font-semibold tabular-nums text-slate-800">
                  {item.avg.toFixed(6)} {item.unit}
                </span>
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function exportSeriesAsCsv(
  series: SeriesByVariable,
  rangeLabel: string,
  districtId: string,
): void {
  const header = ["district_id", "range", "year", "month", "variable", "mean", "min", "max"];
  const rows: string[][] = [header];
  for (const config of VARIABLE_CONFIGS) {
    for (const point of series[config.variable]) {
      rows.push([
        districtId,
        rangeLabel,
        String(point.year),
        String(point.month),
        config.variable,
        point.mean.toFixed(8),
        point.min.toFixed(8),
        point.max.toFixed(8),
      ]);
    }
  }
  const csv = rows.map((row) => row.join(",")).join("\n");
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.setAttribute(
    "download",
    `hydraatlas_${districtId}_${rangeLabel.replace(/[^0-9A-Za-z]+/g, "_")}.csv`,
  );
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}

function exportSeriesAsJson(
  series: SeriesByVariable,
  rangeLabel: string,
  districtId: string,
): void {
  const payload = {
    district_id: districtId,
    range: rangeLabel,
    series: VARIABLE_CONFIGS.map((config) => ({
      variable: config.variable,
      points: series[config.variable],
    })),
  };
  const blob = new Blob([JSON.stringify(payload, null, 2)], {
    type: "application/json;charset=utf-8;",
  });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.setAttribute(
    "download",
    `hydraatlas_${districtId}_${rangeLabel.replace(/[^0-9A-Za-z]+/g, "_")}.json`,
  );
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}

function ExportTab({
  series,
  rangeLabel,
  districtId,
  disabled,
}: {
  series: SeriesByVariable;
  rangeLabel: string;
  districtId: string;
  disabled: boolean;
}) {
  return (
    <div className="mt-5">
      <p className="text-sm text-slate-600 mb-4">
        Download the per-month raster statistics for the selected period:
      </p>
      <div className="flex flex-wrap gap-2">
        <button
          disabled={disabled}
          onClick={() => exportSeriesAsCsv(series, rangeLabel, districtId)}
          className="flex items-center gap-2 px-4 py-2 rounded-md bg-slate-900 text-white text-sm font-medium transition-colors hover:bg-slate-800 disabled:opacity-40 disabled:cursor-not-allowed"
        >
          <span className="material-symbols-rounded" style={{ fontSize: 18 }}>
            table_view
          </span>
          Export CSV
        </button>
        <button
          disabled={disabled}
          onClick={() => exportSeriesAsJson(series, rangeLabel, districtId)}
          className="flex items-center gap-2 px-4 py-2 rounded-md bg-slate-100 text-slate-700 text-sm font-medium transition-colors hover:bg-slate-200 disabled:opacity-40 disabled:cursor-not-allowed"
        >
          <span className="material-symbols-rounded" style={{ fontSize: 18 }}>
            data_object
          </span>
          Export JSON
        </button>
      </div>
      <p className="mt-3 text-xs text-slate-500">
        Each row is keyed by (year, month, variable) and carries mean, min, max.
      </p>
    </div>
  );
}

function RefreshingBadge({ label = "Updating…" }: { label?: string }) {
  return (
    <div
      className="pointer-events-none absolute right-3 top-3 z-10 flex items-center gap-1.5 rounded-full bg-slate-900/80 px-2.5 py-1 text-[11px] font-medium text-white shadow-sm backdrop-blur-sm"
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

function BottomPanel() {
  const selectedStateId = useAppStore((state) => state.selectedStateId);
  const selectedDistrictId = useAppStore((state) => state.selectedDistrictId);
  const bottomPanelOpen = useAppStore((state) => state.bottomPanelOpen);
  const bottomActiveTab = useAppStore((state) => state.bottomActiveTab);
  const setBottomPanelOpen = useAppStore((state) => state.setBottomPanelOpen);
  const setBottomActiveTab = useAppStore((state) => state.setBottomActiveTab);
  const startMonth = useAppStore((state) => state.startMonth);
  const endMonth = useAppStore((state) => state.endMonth);
  const layers = useAppStore((state) => state.layers);

  // The canonical hook subscribes to the shared district data store.
  // Both this panel and the right-side `SelectedLocation` call the
  // same hook with the same args — they share one canonical entry.
  const data = useDistrictData({
    districtId: selectedDistrictId,
    startMonth: startMonth || null,
    endMonth: endMonth || null,
    variables: CANONICAL_VARIABLES,
  });

  // Layer-toggle filtering only affects which charts are VISIBLE. The
  // canonical fetch always covers all three variables so the entry can
  // be shared between both panels.
  const enabledVariables = useMemo(
    () => VARIABLE_CONFIGS.filter((config) => layers[config.layerKey].enabled),
    [layers]
  );
  const noLayerEnabled = enabledVariables.length === 0;

  // Project the canonical store entry into the per-variable
  // SeriesByVariable shape used by the chart builders.
  const series: SeriesByVariable = useMemo(() => {
    const next: SeriesByVariable = { ...EMPTY_SERIES };
    for (const v of CANONICAL_VARIABLES) {
      const r = data.seriesByVariable[v];
      next[v] = r?.points ?? [];
    }
    return next;
  }, [data.seriesByVariable]);

  const monthsProcessed = data.monthsProcessed;

  const seriesChart = useMemo(
    () => buildSeriesChart(series, monthsProcessed, enabledVariables),
    [series, monthsProcessed, enabledVariables]
  );
  const trendChart = useMemo(
    () => buildTrendChart(series, layers.rainfall.enabled),
    [series, layers.rainfall.enabled]
  );

  const rangeLabel = useMemo(() => {
    if (!startMonth || !endMonth) return "";
    return startMonth === endMonth ? startMonth : `${startMonth} → ${endMonth}`;
  }, [startMonth, endMonth]);

  const hasAnyPoints = Object.values(series).some((arr) => arr.length > 0);

  if (!bottomPanelOpen) {
    return (
      <button
        type="button"
        onClick={() => setBottomPanelOpen(true)}
        className="absolute bottom-6 left-1/2 -translate-x-1/2 flex items-center gap-2 rounded-md border border-slate-200 bg-white px-4 py-2.5 text-slate-900 transition-colors hover:bg-slate-50 z-20"
      >
        <span className="material-symbols-rounded text-slate-700" style={{ fontSize: 18 }}>
          show_chart
        </span>
        <span className="text-sm font-medium">Run Analysis</span>
      </button>
    );
  }

  // Distinguish "we have at least one data point to render" from
  // "nothing usable came back". This avoids rendering a misleading
  // empty-state message before the first request resolves.
  const ready = data.ready || hasAnyPoints;
  const showInitialSpinner = !ready && data.loading;
  const showError = !ready && !data.loading && data.error !== null;
  const showNoData = !ready && !data.loading && data.noData;

  return (
    <motion.div
      initial={{ opacity: 0, y: 100 }}
      animate={{ opacity: 1, y: 0 }}
      className="absolute bottom-6 left-0 right-0 mx-auto w-full max-w-[1000px] rounded-md border border-slate-200 bg-white px-5 py-4 z-20"
    >
      <div className="flex items-start justify-between gap-4">
        <div className="flex flex-wrap gap-1">
          {TABS.map((tab) => (
            <button
              key={tab.id}
              onClick={() => setBottomActiveTab(tab.id)}
              className={`flex items-center gap-2 px-3 py-1.5 rounded-md text-sm font-medium transition-colors ${
                bottomActiveTab === tab.id
                  ? "bg-slate-900 text-white"
                  : "text-slate-600 hover:bg-slate-100"
              }`}
            >
              <span className="material-symbols-rounded" style={{ fontSize: 18 }}>
                {tab.icon}
              </span>
              {tab.label}
            </button>
          ))}
        </div>
        <button
          type="button"
          onClick={() => setBottomPanelOpen(false)}
          className="flex h-8 w-8 items-center justify-center rounded-md text-slate-500 transition-colors hover:bg-slate-100"
          aria-label="Collapse panel"
        >
          <span className="material-symbols-rounded" style={{ fontSize: 18 }}>
            expand_more
          </span>
        </button>
      </div>

      {selectedStateId && selectedDistrictId ? (
        <div>
          {hasAnyPoints ? (
            <div className="relative">
              {bottomActiveTab === "time-series" && (
                <TimeSeriesTab chart={seriesChart} visibleConfigs={enabledVariables} />
              )}
              {bottomActiveTab === "trend" && (
                layers.rainfall.enabled ? (
                  <TrendTab chart={trendChart} />
                ) : (
                  <div className="flex items-center justify-center py-10">
                    <p className="text-sm text-slate-500">
                      The Trend tab is rainfall-only. Enable the Rainfall layer to view the linear trend.
                    </p>
                  </div>
                )
              )}
              {bottomActiveTab === "statistics" && (
                <StatisticsTab
                  series={series}
                  monthsProcessed={monthsProcessed}
                  visibleConfigs={enabledVariables}
                />
              )}
              {bottomActiveTab === "export" && (
                <ExportTab
                  series={series}
                  rangeLabel={rangeLabel}
                  districtId={selectedDistrictId}
                  disabled={false}
                />
              )}
              {data.loading && <RefreshingBadge label="Processing new selection…" />}
            </div>
          ) : showInitialSpinner ? (
            <div className="flex items-center justify-center py-10">
              <div className="h-5 w-5 animate-spin rounded-full border-2 border-slate-200 border-t-slate-700" />
            </div>
          ) : showError ? (
            <div className="flex items-center justify-center py-10">
              <p className="text-sm text-slate-500">{data.error}</p>
            </div>
          ) : noLayerEnabled ? (
            <div className="flex items-center justify-center py-10">
              <p className="text-sm text-slate-500">
                Enable at least one layer in Data Explorer to view data.
              </p>
            </div>
          ) : showNoData ? (
            <div className="flex items-center justify-center py-10">
              <p className="text-sm text-slate-500">
                No climate data available for the selected period.
              </p>
            </div>
          ) : (
            <div className="flex items-center justify-center py-10">
              <p className="text-sm text-slate-500">
                No climate data available for the selected period.
              </p>
            </div>
          )}
        </div>
      ) : (
        <div className="mt-5 flex items-center justify-center py-10">
          <p className="text-sm text-slate-500">
            Select a region on the map to view data
          </p>
        </div>
      )}
    </motion.div>
  );
}

export default BottomPanel;
