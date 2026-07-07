/**
 * Regression tests for H1.a (map-instance lifetime) and the
 * demand-driven district architecture.
 *
 * After the refactor, state selection must:
 *   - Create the MapLibre instance exactly once across many state
 *     changes (H1.a — unchanged).
 *   - Load district polygon boundaries (GeoJSON) only.
 *   - NOT trigger any whole-state choropleth raster computation.
 *     The backend `/states/{id}/districts/statistics` endpoint
 *     remains available for explicit on-demand use but must not be
 *     fetched automatically on state selection.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, act } from "@testing-library/react";
import { useAppStore } from "../../stores/useAppStore";

// Mock maplibre-gl with a constructor we can count. The factory is
// invoked lazily by vitest when the component imports maplibre-gl.
vi.mock("maplibre-gl", () => {
  const MockMap = vi.fn().mockImplementation(() => ({
    on: vi.fn(),
    once: vi.fn(),
    off: vi.fn(),
    remove: vi.fn(),
    resize: vi.fn(),
    fitBounds: vi.fn(),
    addSource: vi.fn(),
    addLayer: vi.fn(),
    setData: vi.fn(),
    setFilter: vi.fn(),
    getSource: vi.fn().mockReturnValue({ setData: vi.fn() }),
    getLayer: vi.fn().mockReturnValue(true),
    isStyleLoaded: vi.fn().mockReturnValue(true),
    queryRenderedFeatures: vi.fn().mockReturnValue([]),
  }));
  return {
    default: { Map: MockMap },
    Map: MockMap,
  };
});

// Mock the boundaries API. getStateDistrictRangeStatistics is asserted
// NEVER to be called from the map component after the refactor.
vi.mock("../../api/boundaries", () => ({
  getStates: vi.fn().mockResolvedValue([]),
  getDatasets: vi.fn().mockResolvedValue([]),
  getDistricts: vi.fn().mockResolvedValue([]),
  getDistrictsGeojson: vi.fn().mockResolvedValue({
    type: "FeatureCollection",
    features: [],
  }),
  getStateDistrictRangeStatistics: vi.fn(),
  getDistrictRangeStatistics: vi.fn(),
  getDistrictMonthlySeries: vi.fn(),
}));

import HydraMap from "./HydraMap";
import { Map as MapLibreMap } from "maplibre-gl";
import {
  getStateDistrictRangeStatistics,
  getDistrictsGeojson,
} from "../../api/boundaries";

const mockedStateStats = getStateDistrictRangeStatistics as unknown as ReturnType<typeof vi.fn>;
const mockedGeojson = getDistrictsGeojson as unknown as ReturnType<typeof vi.fn>;

const MockMapCtor = MapLibreMap as unknown as ReturnType<typeof vi.fn>;

beforeEach(() => {
  MockMapCtor.mockClear();
  mockedStateStats.mockClear();
  mockedGeojson.mockClear();
  useAppStore.setState({
    selectedStateId: null,
    selectedDistrictId: null,
    selectedVariable: "precipitation",
    startMonth: "2025-01",
    endMonth: "2025-12",
    availableRange: { minYear: 2025, minMonth: 1, maxYear: 2025, maxMonth: 12 },
    states: [],
    districts: [],
  });
});

describe("HydraMap — H1.a map teardown fix", () => {
  it("creates exactly one Map instance across many state changes", async () => {
    render(<HydraMap />);
    // One constructor call on initial mount.
    expect(MockMapCtor).toHaveBeenCalledTimes(1);

    await act(async () => {
      useAppStore.getState().setSelectedStateId("IND.1.1_1");
    });
    expect(MockMapCtor).toHaveBeenCalledTimes(1);

    await act(async () => {
      useAppStore.getState().setSelectedStateId("IND.2.1_1");
    });
    expect(MockMapCtor).toHaveBeenCalledTimes(1);

    await act(async () => {
      useAppStore.getState().setSelectedStateId("IND.3.1_1");
    });
    expect(MockMapCtor).toHaveBeenCalledTimes(1);

    await act(async () => {
      useAppStore.getState().setSelectedStateId(null);
    });
    expect(MockMapCtor).toHaveBeenCalledTimes(1);
  });

  it("creates exactly one Map instance across district / variable / month changes", async () => {
    render(<HydraMap />);
    expect(MockMapCtor).toHaveBeenCalledTimes(1);

    await act(async () => {
      useAppStore.getState().setSelectedStateId("IND.1.1_1");
      useAppStore.getState().setSelectedDistrictId("IND.1.1.1_1");
    });
    expect(MockMapCtor).toHaveBeenCalledTimes(1);

    await act(async () => {
      useAppStore.getState().setSelectedVariable("soil_moisture");
      useAppStore.getState().setStartMonth("2025-06");
      useAppStore.getState().setEndMonth("2025-08");
    });
    expect(MockMapCtor).toHaveBeenCalledTimes(1);
  });
});

describe("HydraMap — demand-driven state selection", () => {
  it("does NOT call getStateDistrictRangeStatistics on state selection", async () => {
    render(<HydraMap />);

    await act(async () => {
      useAppStore.getState().setSelectedStateId("IND.1.1_1");
    });
    // Allow any microtasks scheduled by the effect to run.
    await act(async () => {});
    expect(mockedStateStats).not.toHaveBeenCalled();
  });

  it("does NOT call getStateDistrictRangeStatistics across many state changes, variable changes, or month-range changes", async () => {
    render(<HydraMap />);

    const stateIds = ["IND.1.1_1", "IND.2.1_1", "IND.3.1_1", "IND.4.1_1"];
    for (const id of stateIds) {
      await act(async () => {
        useAppStore.getState().setSelectedStateId(id);
      });
      await act(async () => {
        useAppStore.getState().setSelectedVariable(
          ["precipitation", "soil_moisture", "surface_runoff"][
            Math.floor(Math.random() * 3)
          ],
        );
      });
      await act(async () => {
        useAppStore.getState().setStartMonth("2025-06");
        useAppStore.getState().setEndMonth("2025-08");
      });
    }
    await act(async () => {});
    expect(mockedStateStats).not.toHaveBeenCalled();
  });

  it("DOES load district polygon boundaries (GeoJSON) on state selection", async () => {
    render(<HydraMap />);

    await act(async () => {
      useAppStore.getState().setSelectedStateId("IND.1.1_1");
    });
    await act(async () => {});

    expect(mockedGeojson).toHaveBeenCalledWith("IND.1.1_1");
  });
});
