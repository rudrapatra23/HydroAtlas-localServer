import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, act } from "@testing-library/react";
import { useAppStore } from "../../stores/useAppStore";

const sourceRegistry = new Map<string, { setData: ReturnType<typeof vi.fn> }>();

vi.mock("maplibre-gl", () => {
  const MockMap = vi.fn().mockImplementation(() => {
    const handlers = new Map<string, Array<(...args: any[]) => void>>();
    return {
      on: vi.fn((event: string, handler: (...args: any[]) => void) => {
        const list = handlers.get(event) ?? [];
        list.push(handler);
        handlers.set(event, list);
        if (event === "load") handler();
      }),
      once: vi.fn((event: string, handler: (...args: any[]) => void) => {
        if (event === "load") handler();
      }),
      off: vi.fn(),
      remove: vi.fn(),
      resize: vi.fn(),
      fitBounds: vi.fn(),
      addSource: vi.fn((id: string) => {
        sourceRegistry.set(id, { setData: vi.fn() });
      }),
      addLayer: vi.fn(),
      setFilter: vi.fn(),
      setPaintProperty: vi.fn(),
      getSource: vi.fn((id: string) => sourceRegistry.get(id)),
      getLayer: vi.fn().mockReturnValue(true),
      isStyleLoaded: vi.fn().mockReturnValue(true),
      queryRenderedFeatures: vi.fn().mockReturnValue([]),
    };
  });
  return {
    default: { Map: MockMap },
    Map: MockMap,
  };
});

vi.mock("../../api/boundaries", () => ({
  getStates: vi.fn().mockResolvedValue([]),
  getDatasets: vi.fn().mockResolvedValue([]),
  getDistricts: vi.fn().mockResolvedValue([]),
  getDistrictsGeojson: vi.fn().mockResolvedValue({
    type: "FeatureCollection",
    features: [],
  }),
  getDistrictRasterClip: vi.fn().mockResolvedValue({
    district_id: "IND.1.1.1_1",
    district_name: "District One",
    state_id: "IND.1.1_1",
    state_name: "State One",
    variable: "precipitation",
    variable_long_name: "Precipitation",
    nc_variable: "tp",
    units: "m",
    year: 2025,
    month: 12,
    time_decoded: "2025-12-01T00:00:00",
    source_resolution_deg: 0.1,
    bbox_used: [0, 0, 1, 1],
    feature_collection: {
      type: "FeatureCollection",
      features: [],
    },
    summary: {
      valid_cells: 1,
      boundary_cells: 0,
      excluded_cells: 0,
      bbox_cells_total: 1,
      mean: 1,
      std: 0,
      min: 1,
      max: 1,
      sum: 1,
      median: 1,
      p25: 1,
      p75: 1,
      partial_geom_count: 0,
    },
    diagnostics: {},
    asset_id: "asset-1",
    asset_storage_key: "era5-land/precipitation/2025/12.nc",
    cache_hit: true,
  }),
  // Range endpoint — returns the same mock shape; the component uses it
  // for multi-month selections (startMonth !== endMonth).
  getDistrictRasterClipRange: vi.fn().mockResolvedValue({
    district_id: "IND.1.1.1_1",
    district_name: "District One",
    state_id: "IND.1.1_1",
    state_name: "State One",
    variable: "precipitation",
    variable_long_name: "Precipitation",
    nc_variable: "tp",
    units: "m",
    year: 2025,
    month: 1,
    time_decoded: "2025-01-01T00:00:00",
    source_resolution_deg: 0.1,
    bbox_used: [0, 0, 1, 1],
    feature_collection: {
      type: "FeatureCollection",
      features: [],
    },
    summary: {
      valid_cells: 1,
      boundary_cells: 0,
      excluded_cells: 0,
      bbox_cells_total: 1,
      mean: 1,
      std: 0,
      min: 1,
      max: 1,
      sum: 1,
      median: 1,
      p25: 1,
      p75: 1,
      partial_geom_count: 0,
    },
    diagnostics: {},
    asset_id: "asset-1",
    asset_storage_key: "era5-land/precipitation/2025/01.nc",
    cache_hit: false,
  }),
  getDistrictRangeStatistics: vi.fn(),
  getStateDistrictRangeStatistics: vi.fn(),
  getDistrictMonthlySeries: vi.fn(),
}));

import HydraMap from "./HydraMap";
import { Map as MapLibreMap } from "maplibre-gl";
import {
  getDistrictRasterClip,
  getDistrictRasterClipRange,
  getDistrictsGeojson,
  getStateDistrictRangeStatistics,
} from "../../api/boundaries";

const mockedGeojson = getDistrictsGeojson as unknown as ReturnType<typeof vi.fn>;
const mockedRasterClip = getDistrictRasterClip as unknown as ReturnType<typeof vi.fn>;
const mockedRasterClipRange = getDistrictRasterClipRange as unknown as ReturnType<typeof vi.fn>;
const mockedStateStats = getStateDistrictRangeStatistics as unknown as ReturnType<typeof vi.fn>;
const MockMapCtor = MapLibreMap as unknown as ReturnType<typeof vi.fn>;

beforeEach(() => {
  sourceRegistry.clear();
  MockMapCtor.mockClear();
  mockedGeojson.mockClear();
  mockedRasterClip.mockClear();
  mockedRasterClipRange.mockClear();
  mockedStateStats.mockClear();
  useAppStore.setState({
    selectedStateId: null,
    selectedDistrictId: null,
    selectedVariable: "precipitation",
    startMonth: "2025-01",
    endMonth: "2025-12",
    availableRange: { minYear: 2025, minMonth: 1, maxYear: 2025, maxMonth: 12 },
    layers: {
      rainfall: { enabled: true },
      "soil-moisture": { enabled: true },
      runoff: { enabled: true },
    },
    states: [],
    districts: [],
  });
});

describe("HydraMap", () => {
  it("creates exactly one Map instance across state, district, variable, and month changes", async () => {
    render(<HydraMap />);
    expect(MockMapCtor).toHaveBeenCalledTimes(1);

    await act(async () => {
      useAppStore.getState().setSelectedStateId("IND.1.1_1");
      useAppStore.getState().setSelectedDistrictId("IND.1.1.1_1");
    });
    expect(MockMapCtor).toHaveBeenCalledTimes(1);

    await act(async () => {
      useAppStore.getState().setSelectedVariable("soil_moisture");
      useAppStore.getState().setEndMonth("2025-08");
    });
    expect(MockMapCtor).toHaveBeenCalledTimes(1);
  });

  it("loads district polygon boundaries on state selection", async () => {
    render(<HydraMap />);

    await act(async () => {
      useAppStore.getState().setSelectedStateId("IND.1.1_1");
    });
    await act(async () => {});

    expect(mockedGeojson).toHaveBeenCalledWith("IND.1.1_1");
  });

  it("fetches district raster cells from the raster-clip-range endpoint for multi-month ranges", async () => {
    render(<HydraMap />);

    await act(async () => {
      useAppStore.getState().setSelectedStateId("IND.1.1_1");
      useAppStore.getState().setSelectedDistrictId("IND.1.1.1_1");
    });
    await act(async () => {});

    // startMonth="2025-01", endMonth="2025-12" → 12 months → range endpoint.
    expect(mockedRasterClipRange).toHaveBeenCalledWith(
      "IND.1.1.1_1",
      {
        start: "2025-01",
        end: "2025-12",
        variable: "precipitation",
      },
    );
    // Single-month endpoint must NOT be called for a multi-month range.
    expect(mockedRasterClip).not.toHaveBeenCalled();
  });

  it("refetches the district raster range when the active raster variable changes", async () => {
    render(<HydraMap />);

    await act(async () => {
      useAppStore.getState().setSelectedStateId("IND.1.1_1");
      useAppStore.getState().setSelectedDistrictId("IND.1.1.1_1");
    });
    await act(async () => {});

    mockedRasterClipRange.mockClear();

    await act(async () => {
      useAppStore.getState().setSelectedVariable("soil_moisture");
    });
    await act(async () => {});

    // startMonth="2025-01", endMonth="2025-12" → still multi-month.
    expect(mockedRasterClipRange).toHaveBeenCalledWith(
      "IND.1.1.1_1",
      {
        start: "2025-01",
        end: "2025-12",
        variable: "soil_moisture",
      },
    );
  });

  it("does not call the old whole-state statistics endpoint", async () => {
    render(<HydraMap />);

    await act(async () => {
      useAppStore.getState().setSelectedStateId("IND.1.1_1");
      useAppStore.getState().setSelectedDistrictId("IND.1.1.1_1");
      useAppStore.getState().setSelectedVariable("surface_runoff");
      useAppStore.getState().setEndMonth("2025-09");
    });
    await act(async () => {});

    expect(mockedStateStats).not.toHaveBeenCalled();
  });
});
