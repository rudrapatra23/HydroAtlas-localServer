import { create } from "zustand";

export type LayerKey = "rainfall" | "soil-moisture" | "runoff";
export type DatasetKey = "era5-land";
export type BottomTab = "time-series" | "trend" | "statistics" | "export";
export type Variable = "precipitation" | "soil_moisture" | "surface_runoff";
export type LoadingScope = "map" | "analysis";
export type RasterViewMode = "average" | "month";

interface LoadingTask {
  phase: string;
  scope: LoadingScope;
  order: number;
}

export interface RegionOption {
  id: string;
  name: string;
}

export interface AvailableRange {
  minYear: number;
  minMonth: number;
  maxYear: number;
  maxMonth: number;
}

/**
 * The fundamental time unit is ONE MONTH. Start / End are stored as
 * ``YYYY-MM`` strings so the picker can never surface individual days,
 * and every analysis request reflects a true inclusive month range.
 */
export type MonthString = string; // e.g. "2024-03"

export interface AppState {
  selectedLayer: LayerKey;
  leftSidebarOpen: boolean;
  rightSidebarOpen: boolean;
  startMonth: MonthString;
  endMonth: MonthString;
  availableRange: AvailableRange | null;
  datasets: Record<DatasetKey, boolean>;
  layers: Record<LayerKey, { enabled: boolean }>;
  bottomPanelOpen: boolean;
  bottomActiveTab: BottomTab;
  states: RegionOption[];
  districts: RegionOption[];
  selectedStateId: string | null;
  selectedDistrictId: string | null;
  selectedVariable: Variable;
  rasterViewMode: RasterViewMode;
  loaderArmed: boolean;
  setSelectedLayer: (layer: LayerKey) => void;
  setLeftSidebarOpen: (isOpen: boolean) => void;
  setRightSidebarOpen: (isOpen: boolean) => void;
  setStartMonth: (month: MonthString) => void;
  setEndMonth: (month: MonthString) => void;
  setAvailableRange: (range: AvailableRange | null) => void;
  toggleDataset: (dataset: DatasetKey) => void;
  toggleLayer: (layer: LayerKey) => void;
  setBottomPanelOpen: (isOpen: boolean) => void;
  setBottomActiveTab: (tab: BottomTab) => void;
  setStates: (states: RegionOption[]) => void;
  setDistricts: (districts: RegionOption[]) => void;
  setSelectedStateId: (id: string | null) => void;
  setSelectedDistrictId: (id: string | null) => void;
  setSelectedVariable: (variable: Variable) => void;
  setRasterViewMode: (mode: RasterViewMode) => void;
  loadingTasks: Record<string, LoadingTask>;
  loadingScope: LoadingScope | null;
  loadingCounter: number;
  loadingPhase: string | null;
  beginLoading: (phase: string, scope: LoadingScope) => string;
  updateLoading: (token: string, phase: string) => void;
  endLoading: (token: string) => void;
}

function resolveActiveLoadingTask(tasks: Record<string, LoadingTask>): LoadingTask | null {
  let active: LoadingTask | null = null;
  for (const task of Object.values(tasks)) {
    if (!active || task.order > active.order) active = task;
  }
  return active;
}

export const useAppStore = create<AppState>((set) => ({
  selectedLayer: "rainfall",
  leftSidebarOpen: true,
  rightSidebarOpen: true,
  // Initialised from the backend's available dataset range on first load
  // (see DataExplorer). Empty until then so the UI never queries with a
  // hardcoded year/month.
  startMonth: "",
  endMonth: "",
  availableRange: null,
  datasets: { "era5-land": true },
  layers: {
    rainfall: { enabled: true },
    "soil-moisture": { enabled: true },
    runoff: { enabled: true },
  },
  bottomPanelOpen: false,
  bottomActiveTab: "time-series",
  states: [],
  districts: [],
  selectedStateId: null,
  selectedDistrictId: null,
  selectedVariable: "precipitation",
  rasterViewMode: "average",
  loaderArmed: false,
  setSelectedLayer: (layer) => set({ selectedLayer: layer }),
  setLeftSidebarOpen: (isOpen) => set({ leftSidebarOpen: isOpen }),
  setRightSidebarOpen: (isOpen) => set({ rightSidebarOpen: isOpen }),
  setStartMonth: (month) => set({ startMonth: month }),
  setEndMonth: (month) => set({ endMonth: month }),
  setAvailableRange: (range) => set({ availableRange: range }),
  toggleDataset: (dataset) =>
    set((state) => ({
      datasets: { ...state.datasets, [dataset]: !state.datasets[dataset] },
    })),
  toggleLayer: (layer) =>
    set((state) => ({
      layers: {
        ...state.layers,
        [layer]: { ...state.layers[layer], enabled: !state.layers[layer].enabled },
      },
    })),
  setBottomPanelOpen: (isOpen) => set({ bottomPanelOpen: isOpen }),
  setBottomActiveTab: (tab) => set({ bottomActiveTab: tab }),
  setStates: (states) => set({ states }),
  setDistricts: (districts) => set({ districts }),
  setSelectedStateId: (id) => set({ selectedStateId: id, selectedDistrictId: null }),
  setSelectedDistrictId: (id) =>
    set((state) => ({
      selectedDistrictId: id,
      loaderArmed: state.loaderArmed || id !== null,
    })),
  setSelectedVariable: (variable) => set({ selectedVariable: variable }),
  setRasterViewMode: (mode) => set({ rasterViewMode: mode }),
  loadingTasks: {},
  loadingScope: null,
  loadingCounter: 0,
  loadingPhase: null,
  beginLoading: (phase, scope) => {
    const token = `${scope}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    set((state) => {
      const nextCounter = state.loadingCounter + 1;
      const loadingTasks = {
        ...state.loadingTasks,
        [token]: { phase, scope, order: nextCounter },
      };
      const active = resolveActiveLoadingTask(loadingTasks);
      return {
        loadingTasks,
        loadingCounter: nextCounter,
        loadingPhase: active?.phase ?? null,
        loadingScope: active?.scope ?? null,
      };
    });
    return token;
  },
  updateLoading: (token, phase) =>
    set((state) => {
      const task = state.loadingTasks[token];
      if (!task) return {};
      const nextCounter = state.loadingCounter + 1;
      const loadingTasks = {
        ...state.loadingTasks,
        [token]: { ...task, phase, order: nextCounter },
      };
      const active = resolveActiveLoadingTask(loadingTasks);
      return {
        loadingTasks,
        loadingCounter: nextCounter,
        loadingPhase: active?.phase ?? null,
        loadingScope: active?.scope ?? null,
      };
    }),
  endLoading: (token) =>
    set((state) => {
      if (!state.loadingTasks[token]) return {};
      const loadingTasks = { ...state.loadingTasks };
      delete loadingTasks[token];
      const active = resolveActiveLoadingTask(loadingTasks);
      return {
        loadingTasks,
        loadingPhase: active?.phase ?? null,
        loadingScope: active?.scope ?? null,
      };
    }),
}));

/**
 * Convert a ``YYYY-MM`` month string (the only format the picker ever
 * produces — ``<input type="month">``) into the ``{year, month}`` tuple
 * the backend's statistics endpoints expect. Returns ``null`` when the
 * input is empty or malformed so callers can defer the API call until
 * a real month is selected.
 */
export function monthStringToYearMonth(
  monthString: string
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

/**
 * Build a ``YYYY-MM`` string from a ``(year, month)`` tuple. Used to
 * seed the month pickers from the backend's available range so the
 * frontend never has to know a hardcoded year or month.
 */
export function yearMonthToMonthString(year: number, month: number): string {
  const mm = String(month).padStart(2, "0");
  return `${year}-${mm}`;
}
