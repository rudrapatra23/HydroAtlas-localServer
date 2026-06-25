import { create } from "zustand";

export type LayerKey = "rainfall" | "soil-moisture" | "runoff";
export type DatasetKey = "era5-land";
export type BottomTab = "time-series" | "trend" | "statistics" | "export";

export interface SelectedPoint {
  lat: number;
  lng: number;
}

export interface RegionOption {
  id: string;
  name: string;
}

export interface AppState {
  selectedPoint: SelectedPoint | null;
  selectedLayer: LayerKey;
  leftSidebarOpen: boolean;
  rightSidebarOpen: boolean;
  timelineDate: string;
  datasets: Record<DatasetKey, boolean>;
  layers: Record<LayerKey, { enabled: boolean; opacity: number }>;
  bottomPanelOpen: boolean;
  bottomActiveTab: BottomTab;
  states: RegionOption[];
  districts: RegionOption[];
  selectedStateId: string | null;
  selectedDistrictId: string | null;
  setSelectedPoint: (point: SelectedPoint | null) => void;
  setSelectedLayer: (layer: LayerKey) => void;
  setLeftSidebarOpen: (isOpen: boolean) => void;
  setRightSidebarOpen: (isOpen: boolean) => void;
  setTimelineDate: (date: string) => void;
  toggleDataset: (dataset: DatasetKey) => void;
  toggleLayer: (layer: LayerKey) => void;
  setLayerOpacity: (layer: LayerKey, opacity: number) => void;
  setBottomPanelOpen: (isOpen: boolean) => void;
  setBottomActiveTab: (tab: BottomTab) => void;
  setStates: (states: RegionOption[]) => void;
  setDistricts: (districts: RegionOption[]) => void;
  setSelectedStateId: (id: string | null) => void;
  setSelectedDistrictId: (id: string | null) => void;
}

const INITIAL_TIMELINE_DATE = "2026-06-15";

export const useAppStore = create<AppState>((set) => ({
  selectedPoint: null,
  selectedLayer: "rainfall",
  leftSidebarOpen: true,
  rightSidebarOpen: true,
  timelineDate: INITIAL_TIMELINE_DATE,
  datasets: { "era5-land": true },
  layers: {
    rainfall: { enabled: true, opacity: 1 },
    "soil-moisture": { enabled: true, opacity: 1 },
    runoff: { enabled: true, opacity: 1 },
  },
  bottomPanelOpen: false,
  bottomActiveTab: "time-series",
  states: [],
  districts: [],
  selectedStateId: null,
  selectedDistrictId: null,
  setSelectedPoint: (point) => set({ selectedPoint: point }),
  setSelectedLayer: (layer) => set({ selectedLayer: layer }),
  setLeftSidebarOpen: (isOpen) => set({ leftSidebarOpen: isOpen }),
  setRightSidebarOpen: (isOpen) => set({ rightSidebarOpen: isOpen }),
  setTimelineDate: (date) => set({ timelineDate: date }),
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
  setLayerOpacity: (layer, opacity) =>
    set((state) => ({
      layers: {
        ...state.layers,
        [layer]: { ...state.layers[layer], opacity },
      },
    })),
  setBottomPanelOpen: (isOpen) => set({ bottomPanelOpen: isOpen }),
  setBottomActiveTab: (tab) => set({ bottomActiveTab: tab }),
  setStates: (states) => set({ states }),
  setDistricts: (districts) => set({ districts }),
  setSelectedStateId: (id) => set({ selectedStateId: id, selectedDistrictId: null }),
  setSelectedDistrictId: (id) => set({ selectedDistrictId: id }),
}));
