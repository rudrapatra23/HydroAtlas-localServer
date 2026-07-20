import { useAppStore, BottomTab, LayerKey } from "../../stores/useAppStore";
import {
  Chart as ChartJS,
  TimeScale,
  LinearScale,
  PointElement,
  LineElement,
  Title,
  Tooltip,
  Legend,
  Filler,
} from "chart.js";

import "chartjs-adapter-date-fns";
import zoomPlugin from "chartjs-plugin-zoom";
import { Line } from "react-chartjs-2";
import { motion } from "framer-motion";
import { useMemo, useRef } from "react";
import { MonthlySeriesPoint } from "../../api/boundaries";
import { useDistrictData } from "../../hooks/useDistrictData";
import {
  getDisplayUnit,
  toDisplayPoint,
  type Variable as CanonicalVariable,
} from "../../stores/districtDataStore";

// standard Chart.js layout components
ChartJS.register(
  TimeScale,
  LinearScale,
  PointElement,
  LineElement,
  Title,
  Tooltip,
  Legend,
  Filler,
  zoomPlugin
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
  { variable: "precipitation", layerKey: "rainfall", label: "Rainfall", color: "#2563EB", icon: "rainy", unit: getDisplayUnit("precipitation") },
  { variable: "soil_moisture", layerKey: "soil-moisture", label: "Soil Moisture", color: "#16A34A", icon: "water_drop", unit: getDisplayUnit("soil_moisture") },
  { variable: "surface_runoff", layerKey: "runoff", label: "Runoff", color: "#EA580C", icon: "waves", unit: getDisplayUnit("surface_runoff") },
];

const CANONICAL_VARIABLES: readonly CanonicalVariable[] = [
  "precipitation",
  "soil_moisture",
  "surface_runoff",
];

type SeriesByVariable = Record<CanonicalVariable, MonthlySeriesPoint[]>;

const EMPTY_SERIES: SeriesByVariable = {
  precipitation: [],
  soil_moisture: [],
  surface_runoff: [],
};

function pointTimestamp(point: MonthlySeriesPoint & { date?: string; day?: number }): number {
  if (point.date) return new Date(point.date).getTime();
  const day = point.day ?? 1;
  return Date.UTC(point.year, point.month - 1, day);
}

type TimeUnit = "day" | "week" | "month" | "year";

function inferTimeUnit(timestamps: number[]): TimeUnit {
  if (timestamps.length < 2) return "month";
  const sorted = [...timestamps].sort((a, b) => a - b);
  const gaps = sorted.slice(1).map((t, i) => t - sorted[i]).filter((g) => g > 0);
  if (gaps.length === 0) return "month";
  gaps.sort((a, b) => a - b);
  const medianGap = gaps[Math.floor(gaps.length / 2)];
  const DAY_MS = 24 * 60 * 60 * 1000;
  if (medianGap <= 1.5 * DAY_MS) return "day";
  if (medianGap <= 9 * DAY_MS) return "week";
  if (medianGap <= 45 * DAY_MS) return "month";
  return "year";
}

const AXIS_DISPLAY_FORMATS: Record<TimeUnit, string> = {
  day: "MMM d",
  week: "MMM d",
  month: "MMM yyyy",
  year: "yyyy",
};

const TOOLTIP_DATE_FORMATS: Record<TimeUnit, string> = {
  day: "MMM d, yyyy",
  week: "'Week of' MMM d, yyyy",
  month: "MMM yyyy",
  year: "yyyy",
};

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


function autoscaleYAxes(chart: ChartJS) {
  const xScale = (chart as any).scales?.x;
  if (!xScale || typeof xScale.min !== "number" || typeof xScale.max !== "number") return;
  const visibleMin = xScale.min;
  const visibleMax = xScale.max;

  const rangesByAxis: Record<string, { min: number; max: number }> = {};

  chart.data.datasets.forEach((dataset: any) => {
    const axisId: string = dataset.yAxisID || "y";
    const points = (dataset.data as { x: number; y: number | null }[]) ?? [];
    const values = points
      .filter(
        (p) =>
          p &&
          p.x >= visibleMin &&
          p.x <= visibleMax &&
          typeof p.y === "number" &&
          Number.isFinite(p.y),
      )
      .map((p) => p.y as number);
    if (values.length === 0) return;

    const localMin = Math.min(...values);
    const localMax = Math.max(...values);
    if (!rangesByAxis[axisId]) {
      rangesByAxis[axisId] = { min: localMin, max: localMax };
    } else {
      rangesByAxis[axisId].min = Math.min(rangesByAxis[axisId].min, localMin);
      rangesByAxis[axisId].max = Math.max(rangesByAxis[axisId].max, localMax);
    }
  });

  let changed = false;
  Object.entries(rangesByAxis).forEach(([axisId, range]) => {
    const scaleOptions = (chart.options.scales as any)?.[axisId];
    if (!scaleOptions) return;
    const spread = range.max - range.min;
    const padding = spread * 0.15 || range.max * 0.1 || 1;
    const newMax = range.max + padding;
    // Rainfall/runoff have a meaningful physical floor at 0 (drought = 0mm);
    // soil moisture rarely approaches 0, so let its floor float instead.
    const newMin = axisId === "y-water" || axisId === "y" ? 0 : Math.max(0, range.min - padding);
    if (scaleOptions.max !== newMax || scaleOptions.min !== newMin) {
      scaleOptions.min = newMin;
      scaleOptions.max = newMax;
      changed = true;
    }
  });

  if (changed) chart.update("none");
}

function buildChartOptions(
  visibleConfigs?: VariableConfig[], 
  timestamps: number[] = [],
  maxValues?: { maxSoil: number; maxWater: number; minSoil?: number }
) {
  const useDualAxis = Array.isArray(visibleConfigs);
  const hasSoil = useDualAxis
    ? !!visibleConfigs!.some((c) => c.variable === "soil_moisture")
    : false;
  const hasWater = useDualAxis
    ? !!visibleConfigs!.some(
        (c) => c.variable === "precipitation" || c.variable === "surface_runoff",
      )
    : true;

  const baseUnit = inferTimeUnit(timestamps);
  const sortedTimestamps = [...timestamps].sort((a, b) => a - b);
  const minTimestamp = sortedTimestamps[0];
  const maxTimestamp = sortedTimestamps[sortedTimestamps.length - 1];

  // Set up dynamic ceilings/floors based on the points found
  const calculatedMaxSoil = maxValues && maxValues.maxSoil > 0 ? maxValues.maxSoil * 1.1 : 30;
  const calculatedMaxWater = maxValues && maxValues.maxWater > 0 ? maxValues.maxWater * 1.1 : undefined;
  const calculatedMinSoil =
    maxValues && typeof maxValues.minSoil === "number"
      ? Math.max(0, maxValues.minSoil - (maxValues.maxSoil - maxValues.minSoil) * 0.15 - 1)
      : 0;

  const scales: Record<string, any> = {
    x: {
      type: "time",
      time: {
        // Don't pin a single granularity — Chart.js recalculates the best-fitting
        // unit (day/week/month/quarter/year) on every zoom 
        
        minUnit: baseUnit,
        tooltipFormat: TOOLTIP_DATE_FORMATS[baseUnit],
        displayFormats: AXIS_DISPLAY_FORMATS,
      },
      grid: { display: false },
      ticks: {
        autoSkip: true,
        maxRotation: 0,
        minRotation: 0,
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
        text: "Soil Moisture (mm)",
        font: { family: "Inter, sans-serif", size: 11 },
        color: "#475569",
      },
      min: calculatedMinSoil,
      max: calculatedMaxSoil,
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
        text: "Rainfall / Runoff (mm)",
        font: { family: "Inter, sans-serif", size: 11 },
        color: "#475569",
      },
      min: 0,
      max: calculatedMaxWater,
      grid: { drawOnChartArea: false },
      ticks: {
        font: { family: "Inter, sans-serif" },
      },
    };
  } else {
    scales["y"] = {
      min: 0,
      max: calculatedMaxWater,
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
      zoom: {
        pan: {
          enabled: true,
          mode: "x" as const,
          threshold: 10,
          onPanComplete: ({ chart }: { chart: ChartJS }) => autoscaleYAxes(chart),
        },
        zoom: {
          wheel: {
            enabled: true,
            speed: 0.05,
          },
          pinch: {
            enabled: true,
          },
          mode: "x" as const,
          onZoomComplete: ({ chart }: { chart: ChartJS }) => autoscaleYAxes(chart),
        },
        limits:
          typeof minTimestamp === "number" && typeof maxTimestamp === "number"
            ? { x: { min: minTimestamp, max: maxTimestamp } }
            : undefined,
      },
    },
  };
}

function buildChartSeries(
  series: SeriesByVariable,
  monthsProcessed: number,
  visibleConfigs: VariableConfig[],
) {
  const reference = visibleConfigs.map((c) => series[c.variable])
    .find((points) => points.length > 0) ?? [];
  const timestamps = reference.map(pointTimestamp);

  return {
    datasets: visibleConfigs.map((config) => {
      const points = series[config.variable];
      const yAxisID =
        config.variable === "soil_moisture" ? "y-soil" : "y-water";
      return {
        label: `${config.label} (${config.unit})`,
        data: points.map((p) => ({ x: pointTimestamp(p), y: p.mean })),
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
    _timestamps: timestamps,
  } as any;
}

function buildTrendChart(series: SeriesByVariable, rainfallEnabled: boolean) {
  if (!rainfallEnabled) {
    return {
      datasets: [],
      _slope: null,
      _intercept: null,
      _timestamps: [],
    } as any;
  }

  const rainfall = series.precipitation;
  const timestamps = rainfall.map(pointTimestamp);
  const regression = linearRegression(rainfall);
  const trendData = regression
    ? rainfall.map((p, i) => ({
        x: pointTimestamp(p),
        y: regression.slope * i + regression.intercept,
      }))
    : [];

  return {
    datasets: [
      {
        label: "Rainfall (observed)",
        data: rainfall.map((p) => ({ x: pointTimestamp(p), y: p.mean })),
        borderColor: "#2563EB",
        backgroundColor: "transparent",
        tension: 0.35,
        borderWidth: 2,
        pointRadius: 0,
      },
      {
        label: "Rainfall (linear trend)",
        data: trendData,
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
    _timestamps: timestamps,
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



function TimeSeriesTab({ 
  chart, 
  visibleConfigs,
  maxValues
}: { 
  chart: any; 
  visibleConfigs: VariableConfig[];
  maxValues: { maxSoil: number; maxWater: number; minSoil?: number };
}) {
  const chartRef = useRef<any>(null);
  const options = useMemo(
    () => buildChartOptions(visibleConfigs, chart._timestamps, maxValues),
    [chart._timestamps, visibleConfigs, maxValues.maxSoil, maxValues.maxWater, maxValues.minSoil],
  );
  return (
    <div className="relative min-h-[16rem] mt-4 select-none">
      <button
        type="button"
        title="Reset zoom"
        onClick={() => {
          const c = chartRef.current;
          if (c) {
            c.resetZoom();
            // Optional autoscale, need to inline or call if still present
            // Assuming autoscaleYAxes is available
            autoscaleYAxes(c);
          }
        }}
        className="absolute top-1 right-28 z-10 flex h-6 w-6 items-center justify-center rounded-md bg-white text-slate-500 shadow-sm border border-slate-200 transition-colors hover:bg-slate-100 hover:text-slate-900"
      >
        <span className="material-symbols-rounded" style={{ fontSize: 14 }}>
          restart_alt
        </span>
      </button>
      <Line ref={chartRef} data={chart} options={options} />
    </div>
  );
}

function TrendTab({ 
  chart,
  maxValues
}: { 
  chart: any;
  maxValues: { maxSoil: number; maxWater: number; minSoil?: number };
}) {
  const chartRef = useRef<any>(null);
  const options = useMemo(
    () => buildChartOptions(undefined, chart._timestamps, maxValues),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [chart._timestamps, maxValues.maxSoil, maxValues.maxWater, maxValues.minSoil],
  );
  return (
    <div className="relative min-h-[16rem] mt-4 select-none">
      <button
        type="button"
        title="Reset zoom"
        onClick={() => {
          const c = chartRef.current;
          if (c) {
            c.resetZoom();
            autoscaleYAxes(c);
          }
        }}
        className="absolute top-1 right-[200px] z-10 flex h-6 w-6 items-center justify-center rounded-md bg-white text-slate-500 shadow-sm border border-slate-200 transition-colors hover:bg-slate-100 hover:text-slate-900"
      >
        <span className="material-symbols-rounded" style={{ fontSize: 14 }}>
          restart_alt
        </span>
      </button>
      <Line ref={chartRef} data={chart} options={options} />
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

  const data = useDistrictData({
    districtId: selectedDistrictId,
    startMonth: startMonth || null,
    endMonth: endMonth || null,
    variables: CANONICAL_VARIABLES,
  });

  const enabledVariables = useMemo(
    () => VARIABLE_CONFIGS.filter((config) => layers[config.layerKey].enabled),
    [layers]
  );
  const noLayerEnabled = enabledVariables.length === 0;

  const series: SeriesByVariable = useMemo(() => {
    const next: SeriesByVariable = { ...EMPTY_SERIES };
    for (const v of CANONICAL_VARIABLES) {
      const r = data.seriesByVariable[v];
      next[v] = r?.points ?? [];
    }
    return next;
  }, [data.seriesByVariable]);

  const displaySeries: SeriesByVariable = useMemo(() => {
    const next: SeriesByVariable = { ...EMPTY_SERIES };
    for (const v of CANONICAL_VARIABLES) {
      next[v] = series[v].map((point) => toDisplayPoint(v, point));
    }
    return next;
  }, [series]);

  // Scan across data points to pick the true absolute max/min property values for the y-axis bounds
  const maxValues = useMemo(() => {
    let maxSoil = 0;
    let maxWater = 0;
    let minSoil = Infinity;

    displaySeries.soil_moisture.forEach((p) => {
      if (p.max > maxSoil) maxSoil = p.max;
      if (p.min < minSoil) minSoil = p.min;
    });
    displaySeries.precipitation.forEach((p) => { if (p.max > maxWater) maxWater = p.max; });
    displaySeries.surface_runoff.forEach((p) => { if (p.max > maxWater) maxWater = p.max; });

    return { maxSoil, maxWater, minSoil: Number.isFinite(minSoil) ? minSoil : 0 };
  }, [displaySeries]);

  const monthsProcessed = data.monthsProcessed;

  const seriesChart = useMemo(
    () => buildChartSeries(displaySeries, monthsProcessed, enabledVariables),
    [displaySeries, monthsProcessed, enabledVariables]
  );
  const trendChart = useMemo(
    () => buildTrendChart(displaySeries, layers.rainfall.enabled),
    [displaySeries, layers.rainfall.enabled]
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
        className="absolute bottom-6 left-1/2 -translate-x-1/2 flex h-10 items-center gap-2 rounded-full border border-slate-200 bg-white/95 px-4 text-sm font-semibold text-slate-800 shadow-lg shadow-slate-900/10 backdrop-blur-xl transition hover:bg-white z-20"
      >
        <span className="material-symbols-rounded text-slate-700" style={{ fontSize: 18 }}>
          show_chart
        </span>
        <span className="text-sm font-medium">Run Analysis</span>
      </button>
    );
  }

  const ready = data.ready || hasAnyPoints;
  const showInitialSpinner = !ready && data.loading;
  const showError = !ready && !data.loading && data.error !== null;
  const showNoData = !ready && !data.loading && data.noData;

  return (
    <motion.div
      initial={{ opacity: 0, y: 100 }}
      animate={{ opacity: 1, y: 0 }}
      className="absolute bottom-6 left-0 right-0 mx-auto w-full max-w-[1000px] rounded-2xl border border-slate-200/80 bg-white/95 shadow-2xl shadow-slate-900/10 backdrop-blur-xl px-5 py-4 z-20"
    >
      <div className="flex items-start justify-between gap-4">
        <div className="flex flex-wrap gap-1">
          {TABS.map((tab) => (
            <button
              key={tab.id}
              onClick={() => setBottomActiveTab(tab.id)}
              className={`flex items-center gap-2 px-3 py-1.5 rounded-full text-sm font-medium transition-colors ${
                bottomActiveTab === tab.id
                  ? "bg-slate-900 text-white"
                  : "text-slate-600 hover:bg-slate-100 hover:text-slate-900"
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
          className="flex h-8 w-8 items-center justify-center rounded-full text-slate-500 transition-colors hover:bg-slate-100 hover:text-slate-900"
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
                <TimeSeriesTab 
                  chart={seriesChart} 
                  visibleConfigs={enabledVariables} 
                  maxValues={maxValues} 
                />
              )}
              {bottomActiveTab === "trend" && (
                layers.rainfall.enabled ? (
                  <TrendTab chart={trendChart} maxValues={maxValues} />
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
                  series={displaySeries}
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
              {data.loading && null}
            </div>
          ) : showInitialSpinner ? (
            <div className="py-10" />
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