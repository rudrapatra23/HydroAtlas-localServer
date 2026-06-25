import { create } from "zustand";

export type LayerKey = "rainfall" | "soil-moisture" | "runoff";
export type DatasetKey = "era5-land";
export type BottomTab = "time-series" | "trend" | "statistics" | "export";
export type Variable = "precipitation" | "soil_moisture" | "surface_runoff";

export interface RegionOption {
  id: string;
  name: string;
}

export interface DistrictStats {
  mean: number;
  min: number;
  max: number;
}

export interface StateDistrictStatisticsItem {
  district_id: string;
  mean: number;
  min: number;
  max: number;
}

export interface StateDistrictStatistics {
  state_id: string;
  year: number;
  month: number;
  variable: Variable;
  districts: StateDistrictStatisticsItem[];
}

export interface AppState {
  selectedLayer: LayerKey;
  leftSidebarOpen: boolean;
  rightSidebarOpen: boolean;
  startDate: string;
  endDate: string;
  datasets: Record<DatasetKey, boolean>;
  layers: Record<LayerKey, { enabled: boolean }>;
  bottomPanelOpen: boolean;
  bottomActiveTab: BottomTab;
  states: RegionOption[];
  districts: RegionOption[];
  selectedStateId: string | null;
  selectedDistrictId: string | null;
  selectedVariable: Variable;
  selectedYear: number;
  selectedMonth: number;
  stateDistrictStatistics: StateDistrictStatistics | null;
  districtStats: DistrictStats | null;
  setSelectedLayer: (layer: LayerKey) => void;
  setLeftSidebarOpen: (isOpen: boolean) => void;
  setRightSidebarOpen: (isOpen: boolean) => void;
  setStartDate: (date: string) => void;
  setEndDate: (date: string) => void;
  toggleDataset: (dataset: DatasetKey) => void;
  toggleLayer: (layer: LayerKey) => void;
  setBottomPanelOpen: (isOpen: boolean) => void;
  setBottomActiveTab: (tab: BottomTab) => void;
  setStates: (states: RegionOption[]) => void;
  setDistricts: (districts: RegionOption[]) => void;
  setSelectedStateId: (id: string | null) => void;
  setSelectedDistrictId: (id: string | null) => void;
  setSelectedVariable: (variable: Variable) => void;
  setSelectedYear: (year: number) => void;
  setSelectedMonth: (month: number) => void;
  setStateDistrictStatistics: (stats: StateDistrictStatistics | null) => void;
  setDistrictStats: (stats: DistrictStats | null) => void;
}

const INITIAL_START_DATE = "2026-06-01";
const INITIAL_END_DATE = "2026-06-25";

export const useAppStore = create<AppState>((set) => ({
  selectedLayer: "rainfall",
  leftSidebarOpen: true,
  rightSidebarOpen: true,
  startDate: INITIAL_START_DATE,
  endDate: INITIAL_END_DATE,
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
  selectedYear: 2026,
  selectedMonth: 5,
  stateDistrictStatistics: null,
  districtStats: null,
  setSelectedLayer: (layer) => set({ selectedLayer: layer }),
  setLeftSidebarOpen: (isOpen) => set({ leftSidebarOpen: isOpen }),
  setRightSidebarOpen: (isOpen) => set({ rightSidebarOpen: isOpen }),
  setStartDate: (date) => set({ startDate: date }),
  setEndDate: (date) => set({ endDate: date }),
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
  setSelectedDistrictId: (id) => set({ selectedDistrictId: id }),
  setSelectedVariable: (variable) => set({ selectedVariable: variable }),
  setSelectedYear: (year) => set({ selectedYear: year }),
  setSelectedMonth: (month) => set({ selectedMonth: month }),
  setStateDistrictStatistics: (stats) => set({ stateDistrictStatistics: stats }),
  setDistrictStats: (stats) => set({ districtStats: stats }),
}));
