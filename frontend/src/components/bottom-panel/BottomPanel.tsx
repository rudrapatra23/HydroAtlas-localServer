import { useAppStore, BottomTab } from "../../stores/useAppStore";
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

function getMockTimeSeries(lat: number, lng: number) {
  const labels = Array.from({ length: 30 }, (_, i) => {
    const d = new Date("2026-06-01");
    d.setDate(d.getDate() + i);
    return d.toISOString().split("T")[0];
  });

  const seed = lat * 10000 + lng * 100;
  const random = (i: number, min: number, max: number) => {
    const x = Math.sin(seed + i) * 10000;
    return (x - Math.floor(x)) * (max - min) + min;
  };

  return {
    labels,
    rainfall: labels.map((_, i) => parseFloat(random(i, 10, 200).toFixed(1))),
    soilMoisture: labels.map((_, i) => parseFloat(random(i + 100, 20, 80).toFixed(1))),
    runoff: labels.map((_, i) => parseFloat(random(i + 200, 5, 100).toFixed(1))),
  };
}

function TimeSeriesTab({ lat, lng }: { lat: number; lng: number }) {
  const data = getMockTimeSeries(lat, lng);
  return (
    <div className="min-h-[16rem] mt-4">
      <Line
        data={{
          labels: data.labels,
          datasets: [
            {
              label: "Rainfall (mm)",
              data: data.rainfall,
              borderColor: "#2563EB",
              backgroundColor: "rgba(37, 99, 235, 0.10)",
              fill: true,
              tension: 0.35,
              borderWidth: 2,
              pointRadius: 0,
              pointHoverRadius: 5,
            },
            {
              label: "Soil Moisture (%)",
              data: data.soilMoisture,
              borderColor: "#16A34A",
              backgroundColor: "transparent",
              fill: false,
              tension: 0.35,
              borderWidth: 2,
              pointRadius: 0,
              pointHoverRadius: 5,
            },
            {
              label: "Runoff (mm)",
              data: data.runoff,
              borderColor: "#EA580C",
              backgroundColor: "transparent",
              fill: false,
              tension: 0.35,
              borderWidth: 2,
              pointRadius: 0,
              pointHoverRadius: 5,
            },
          ],
        }}
        options={{
          responsive: true,
          maintainAspectRatio: false,
          interaction: {
            intersect: false,
            mode: "index",
          },
          scales: {
            x: {
              grid: { display: false },
              ticks: {
                maxTicksLimit: 6,
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
              position: "top",
              align: "end",
              labels: {
                boxWidth: 8,
                boxHeight: 8,
                usePointStyle: true,
                pointStyle: "circle",
                font: {
                  family: "Inter, sans-serif",
                  size: 12,
                  weight: 500 as const,
                },
                padding: 16,
              },
            },
            tooltip: {
              backgroundColor: "rgba(15, 23, 42, 0.90)",
              padding: 12,
              cornerRadius: 10,
              titleFont: {
                family: "Inter, sans-serif",
                weight: 600 as const,
              },
              bodyFont: {
                family: "Inter, sans-serif",
              },
            },
          },
        }}
      />
    </div>
  );
}

function TrendTab({ lat, lng }: { lat: number; lng: number }) {
  const data = getMockTimeSeries(lat, lng);
  return (
    <div className="min-h-[16rem] mt-4">
      <Line
        data={{
          labels: data.labels,
          datasets: [
            {
              label: "Rainfall Trend",
              data: data.rainfall,
              borderColor: "#2563EB",
              backgroundColor: "transparent",
              borderDash: [6, 6],
              tension: 0.2,
              borderWidth: 2,
              pointRadius: 0,
            },
          ],
        }}
        options={{
          responsive: true,
          maintainAspectRatio: false,
          scales: {
            x: { grid: { display: false } },
            y: { grid: { color: "rgba(15, 23, 42, 0.06)" } },
          },
          plugins: {
            legend: {
              position: "top",
              align: "end",
              labels: {
                boxWidth: 8,
                font: {
                  family: "Inter, sans-serif",
                  size: 12,
                  weight: 500 as const,
                },
                padding: 16,
              },
            },
          },
        }}
      />
    </div>
  );
}

function StatisticsTab({ lat, lng }: { lat: number; lng: number }) {
  const data = getMockTimeSeries(lat, lng);
  const stats = (arr: number[]) => ({
    min: Math.min(...arr),
    max: Math.max(...arr),
    avg: parseFloat((arr.reduce((a, b) => a + b, 0) / arr.length).toFixed(1)),
  });
  const rainStats = stats(data.rainfall);
  const soilStats = stats(data.soilMoisture);
  const runoffStats = stats(data.runoff);

  const statItems = [
    {
      label: "Rainfall",
      color: "#2563EB",
      icon: "rainy",
      unit: "mm",
      ...rainStats,
    },
    {
      label: "Soil Moisture",
      color: "#16A34A",
      icon: "water_drop",
      unit: "%",
      ...soilStats,
    },
    {
      label: "Runoff",
      color: "#EA580C",
      icon: "waves",
      unit: "mm",
      ...runoffStats,
    },
  ];

  return (
    <div className="mt-5 grid grid-cols-1 sm:grid-cols-3 gap-4">
      {statItems.map((item) => (
        <div
          key={item.label}
          className="rounded-[16px] border border-slate-900/6 bg-slate-50/60 p-5"
        >
          <div className="flex items-center gap-2 mb-4">
            <div
              className="flex h-9 w-9 items-center justify-center rounded-[12px]"
              style={{ backgroundColor: `${item.color}14` }}
            >
              <span
                className="material-symbols-rounded"
                style={{ fontSize: 24, color: item.color }}
              >
                {item.icon}
              </span>
            </div>
            <span className="text-[11px] font-semibold text-slate-500 uppercase tracking-[0.14em]">
              {item.label}
            </span>
          </div>
          <div className="space-y-3">
            <div className="flex justify-between">
              <span className="text-xs text-slate-500">Minimum</span>
              <span className="text-sm font-semibold text-slate-800">
                {item.min} {item.unit}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-xs text-slate-500">Maximum</span>
              <span className="text-sm font-semibold text-slate-800">
                {item.max} {item.unit}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-xs text-slate-500">Average</span>
              <span className="text-sm font-semibold text-slate-800">
                {item.avg} {item.unit}
              </span>
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

function ExportTab() {
  return (
    <div className="mt-5">
      <p className="text-sm text-slate-600 mb-4">
        Export your data in various formats:
      </p>
      <div className="flex flex-wrap gap-3">
        <motion.button
          whileHover={{ scale: 1.02 }}
          whileTap={{ scale: 0.98 }}
          className="flex items-center gap-2 px-5 py-2.5 rounded-[12px] bg-slate-900 text-white text-sm font-semibold transition"
        >
          <span className="material-symbols-rounded" style={{ fontSize: 20 }}>
            table_view
          </span>
          Export CSV
        </motion.button>
        <motion.button
          whileHover={{ scale: 1.02 }}
          whileTap={{ scale: 0.98 }}
          className="flex items-center gap-2 px-5 py-2.5 rounded-[12px] bg-slate-100 text-slate-700 text-sm font-semibold transition hover:bg-slate-200"
        >
          <span className="material-symbols-rounded" style={{ fontSize: 20 }}>
            data_object
          </span>
          Export JSON
        </motion.button>
      </div>
    </div>
  );
}

function BottomPanel() {
  const selectedPoint = useAppStore((state) => state.selectedPoint);
  const bottomPanelOpen = useAppStore((state) => state.bottomPanelOpen);
  const bottomActiveTab = useAppStore((state) => state.bottomActiveTab);
  const setBottomPanelOpen = useAppStore((state) => state.setBottomPanelOpen);
  const setBottomActiveTab = useAppStore((state) => state.setBottomActiveTab);

  if (!bottomPanelOpen) {
    return (
      <motion.button
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        whileHover={{ scale: 1.02, y: -2 }}
        whileTap={{ scale: 0.98 }}
        type="button"
        onClick={() => setBottomPanelOpen(true)}
        className="absolute bottom-6 left-1/2 -translate-x-1/2 flex items-center gap-2.5 rounded-full border border-slate-900/6 bg-white/92 px-5 py-3 shadow-[0_12px_40px_rgba(15,23,42,0.08)] backdrop-blur-[22px] transition z-20"
      >
        <span className="material-symbols-rounded text-slate-700" style={{ fontSize: 20 }}>
          show_chart
        </span>
        <span className="text-sm font-semibold text-slate-900">
          Open Analytics
        </span>
      </motion.button>
    );
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 100 }}
      animate={{ opacity: 1, y: 0 }}
      whileHover={{ y: -1, boxShadow: "0 16px 50px rgba(15,23,42,0.12)" }}
      className="absolute bottom-6 left-0 right-0 mx-auto w-full max-w-[1000px] rounded-[24px] border border-slate-900/6 bg-white/92 px-6 py-5 shadow-[0_12px_40px_rgba(15,23,42,0.08)] backdrop-blur-[22px] z-20 transition-all duration-180 ease-out"
    >
      <div className="flex items-start justify-between gap-4">
        <div className="flex flex-wrap gap-1.5">
          {TABS.map((tab) => (
            <motion.button
              key={tab.id}
              whileHover={{ scale: 1.02 }}
              whileTap={{ scale: 0.97 }}
              onClick={() => setBottomActiveTab(tab.id)}
              className={`flex items-center gap-2 px-4 py-2 rounded-full text-sm font-semibold transition ${
                bottomActiveTab === tab.id
                  ? "bg-slate-900 text-white"
                  : "text-slate-600 hover:bg-slate-100"
              }`}
            >
              <span className="material-symbols-rounded" style={{ fontSize: 20 }}>
                {tab.icon}
              </span>
              {tab.label}
            </motion.button>
          ))}
        </div>
        <motion.button
          whileHover={{ scale: 1.05, backgroundColor: "rgba(15,23,42,0.04)" }}
          whileTap={{ scale: 0.95 }}
          type="button"
          onClick={() => setBottomPanelOpen(false)}
          className="flex h-9 w-9 items-center justify-center rounded-full transition"
        >
          <span className="material-symbols-rounded text-slate-500" style={{ fontSize: 20 }}>
            expand_more
          </span>
        </motion.button>
      </div>

      {selectedPoint ? (
        <motion.div
          key={bottomActiveTab}
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.2 }}
        >
          {bottomActiveTab === "time-series" && (
            <TimeSeriesTab lat={selectedPoint.lat} lng={selectedPoint.lng} />
          )}
          {bottomActiveTab === "trend" && (
            <TrendTab lat={selectedPoint.lat} lng={selectedPoint.lng} />
          )}
          {bottomActiveTab === "statistics" && (
            <StatisticsTab lat={selectedPoint.lat} lng={selectedPoint.lng} />
          )}
          {bottomActiveTab === "export" && <ExportTab />}
        </motion.div>
      ) : (
        <div className="mt-5 flex items-center justify-center py-10">
          <p className="text-sm text-slate-500">
            Select a point on the map to view data
          </p>
        </div>
      )}
    </motion.div>
  );
}

export default BottomPanel;
