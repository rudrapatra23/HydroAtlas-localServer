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
import { useEffect, useMemo, useRef, useState } from "react";
import {
  DistrictMonthlySeries,
  MonthlySeriesPoint,
  getDistrictMonthlySeries,
} from "../../api/boundaries";

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
  variable: Variable;
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
 * Variable ↔ LayerKey mapping used to filter API requests and chart
 * datasets by which layer toggles are enabled in Data Explorer. Lives
 * at module scope so the dependency array of BottomPanel's fetch
 * effect can reference the same set of keys.
 */
const VARIABLE_TO_LAYER: Record<Variable, LayerKey> = {
  precipitation: "rainfall",
  soil_moisture: "soil-moisture",
  surface_runoff: "runoff",
};

type SeriesByVariable = Record<Variable, MonthlySeriesPoint[]>;

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

function buildChartOptions() {
  return {
    responsive: true,
    maintainAspectRatio: false,
    interaction: {
      intersect: false,
      mode: "index" as const,
    },
    scales: {
      x: {
        grid: { display: false },
        ticks: {
          maxTicksLimit: 8,
          font: { family: "Inter, sans-serif" },
        },
      },
      y: {
        grid: { color: "rgba(15, 23, 42, 0.06)" },
        ticks: {
          font: { family: "Inter, sans-serif" },
        },
      },
    },
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
      return {
        label: `${config.label} (${config.unit})`,
        data: points.map((p) => p.mean),
        borderColor: config.color,
        backgroundColor: config.variable === "precipitation" ? `${config.color}1A` : "transparent",
        fill: config.variable === "precipitation",
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
  // The Trend tab is rainfall-only by design. If the rainfall layer is
  // off we still emit a structurally valid chart with empty datasets so
  // the caller can render a clear empty state instead of a chart that
  // appears to show data when none is configured.
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

function TimeSeriesTab({ chart }: { chart: any }) {
  return (
    <div className="min-h-[16rem] mt-4">
      <Line data={chart} options={buildChartOptions()} />
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

/**
 * Small, non-blocking overlay rendered while a new query is in flight.
 * The previous committed chart / KPI data stays visible underneath so
 * the user never sees a destructive blank during district / month /
 * year / range / layer-toggle transitions. Truthful: only signals that
 * a refresh is in progress, never a fake percentage or fabricated
 * backend stage.
 */
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

  // Layer-toggle filtering: only enabled variables are fetched and
  // rendered. If every layer is off we short-circuit to an empty state
  // before issuing any API call.
  //
  // MEMOIZED: previously this was a plain ``.filter(...)`` call. React
  // allocates a fresh array on every render, and because this array is
  // in the useEffect dep list it caused the effect to re-fire on every
  // render — which then issued another batch of network requests,
  // which triggered another ``setSeries`` re-render, and so on
  // indefinitely. ``useMemo`` with ``[layers]`` keeps the array
  // identity stable until the user actually toggles a layer.
  const enabledVariables = useMemo(
    () => VARIABLE_CONFIGS.filter((config) => layers[config.layerKey].enabled),
    [layers]
  );
  const noLayerEnabled = enabledVariables.length === 0;

  // Holds the AbortController for the in-flight batch of requests. The
  // useEffect cleanup aborts it so a rapid re-fire cannot leak an
  // unanswered request that keeps the network stack busy even after
  // the user moves on.
  const abortRef = useRef<AbortController | null>(null);

  const [series, setSeries] = useState<SeriesByVariable>(EMPTY_SERIES);
  const [loading, setLoading] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [monthsProcessed, setMonthsProcessed] = useState(0);
  // Tracks whether at least one fetch for the current selection has
  // ever settled. Used to distinguish "no data yet attempted" from a
  // genuine empty / error result so we never render a misleading
  // "No climate data available" message before the first request
  // resolves.
  const [hasAttempted, setHasAttempted] = useState(false);

  // Every change to Start Month, End Month, selected district, or layer
  // toggles must immediately re-fetch the per-month series for the
  // currently-enabled variables only.
  useEffect(() => {
    if (!selectedDistrictId) {
      setSeries(EMPTY_SERIES);
      setErrorMessage(null);
      setMonthsProcessed(0);
      setLoading(false);
      return;
    }

    if (!startMonth || !endMonth) {
      setSeries(EMPTY_SERIES);
      setErrorMessage(null);
      return;
    }

    if (noLayerEnabled) {
      // Short-circuit: no API calls when every layer is off.
      setSeries(EMPTY_SERIES);
      setErrorMessage(null);
      setMonthsProcessed(0);
      setLoading(false);
      return;
    }

    const startKey = Number(startMonth.slice(0, 4)) * 12 + Number(startMonth.slice(5, 7));
    const endKey = Number(endMonth.slice(0, 4)) * 12 + Number(endMonth.slice(5, 7));
    if (startKey > endKey) {
      setSeries(EMPTY_SERIES);
      setErrorMessage("Start Month must be on or before End Month.");
      setMonthsProcessed(0);
      setLoading(false);
      return;
    }

    const districtId = selectedDistrictId;
    const [startYear, startMonthNum] = [
      Number(startMonth.slice(0, 4)),
      Number(startMonth.slice(5, 7)),
    ];
    const [endYear, endMonthNum] = [
      Number(endMonth.slice(0, 4)),
      Number(endMonth.slice(5, 7)),
    ];
    const range = { start_year: startYear, start_month: startMonthNum, end_year: endYear, end_month: endMonthNum };

    let cancelled = false;
    // Abort any in-flight batch from the previous effect run so a rapid
    // re-fire cannot leave an unanswered request on the wire.
    abortRef.current?.abort();
    const ac = new AbortController();
    abortRef.current = ac;

    setLoading(true);
    setErrorMessage(null);

    async function fetchEnabledSeries() {
      try {
        const responses: [Variable, DistrictMonthlySeries][] = await Promise.all(
          enabledVariables.map((config) =>
            getDistrictMonthlySeries(
              districtId,
              { ...range, variable: config.variable },
              ac.signal
            )
              .then((r) => [config.variable, r] as [Variable, DistrictMonthlySeries])
          )
        );
        if (cancelled) return;
        const next: SeriesByVariable = {
          precipitation: [],
          soil_moisture: [],
          surface_runoff: [],
        };
        let processed = 0;
        for (const [variable, response] of responses) {
          next[variable] = response.points;
          if (response.months_processed > processed) {
            processed = response.months_processed;
          }
        }
        setSeries(next);
        setMonthsProcessed(processed);
        setHasAttempted(true);
      } catch (error) {
        if (cancelled) return;
        const message = error instanceof Error ? error.message : String(error);
        // AbortError is expected when the next effect run cancels this
        // batch — not a real failure, just skip the user-visible error.
        if (/AbortError/i.test(message)) return;
        if (/404/.test(message)) {
          setSeries(EMPTY_SERIES);
          setMonthsProcessed(0);
          setErrorMessage("No climate data available for the selected period.");
        } else {
          console.error("Failed to fetch monthly series:", error);
          setSeries(EMPTY_SERIES);
          setMonthsProcessed(0);
          setErrorMessage("Failed to load data. Please try a different period.");
        }
        setHasAttempted(true);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    fetchEnabledSeries();

    return () => {
      cancelled = true;
      ac.abort();
    };
    // ``enabledVariables`` is derived from ``layers`` so adding it to the
    // dependency list is sufficient — toggling any layer triggers a
    // re-fetch with the new enabled set.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedDistrictId, startMonth, endMonth, enabledVariables]);

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
          {monthsProcessed > 0 ? (
            <div className="relative">
              {bottomActiveTab === "time-series" && (
                <TimeSeriesTab chart={seriesChart} />
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
              {loading && <RefreshingBadge label="Processing new selection…" />}
            </div>
          ) : loading || !hasAttempted ? (
            <div className="flex items-center justify-center py-10">
              <div className="h-5 w-5 animate-spin rounded-full border-2 border-slate-200 border-t-slate-700" />
            </div>
          ) : errorMessage ? (
            <div className="flex items-center justify-center py-10">
              <p className="text-sm text-slate-500">{errorMessage}</p>
            </div>
          ) : noLayerEnabled ? (
            <div className="flex items-center justify-center py-10">
              <p className="text-sm text-slate-500">
                Enable at least one layer in Data Explorer to view data.
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
