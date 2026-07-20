import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, act, waitFor, screen } from "@testing-library/react";
import { useAppStore } from "../../stores/useAppStore";
import { useDistrictDataStore } from "../../stores/districtDataStore";

type Pending = {
  promise: Promise<any>;
  resolve: (value: any) => void;
  reject: (error: any) => void;
  signal?: AbortSignal;
};
const pendingByKey = new Map<string, Pending[]>();

function defaultSeriesResponse(
  districtId: string,
  variable: string,
  monthlyMeans: number[],
) {
  return {
    district_id: districtId,
    variable,
    start_year: 2025,
    start_month: 1,
    end_year: 2025,
    end_month: 12,
    months_processed: monthlyMeans.length,
    points: monthlyMeans.map((m, idx) => ({
      year: 2025,
      month: idx + 1,
      mean: m,
      min: m - 1,
      max: m + 1,
    })),
  };
}

vi.mock("../../api/boundaries", () => ({
  getStates: vi.fn().mockResolvedValue([]),
  getDatasets: vi.fn().mockResolvedValue([]),
  getDistricts: vi.fn().mockResolvedValue([]),
  getDistrictsGeojson: vi.fn().mockResolvedValue({ type: "FeatureCollection", features: [] }),
  getDistrictRangeStatistics: vi.fn(),
  getStateDistrictRangeStatistics: vi.fn(),
  getDistrictMonthlySeries: vi.fn(
    (districtId: string, body: any, signal?: AbortSignal) => {
      let resolve!: (v: any) => void;
      let reject!: (e: any) => void;
      const promise = new Promise<any>((res, rej) => {
        resolve = res;
        reject = rej;
      });
      const key = `${districtId}|${body.variable}`;
      const list = pendingByKey.get(key) ?? [];
      const entry: Pending = { promise, resolve, reject, signal };
      list.push(entry);
      pendingByKey.set(key, list);
      if (signal) {
        signal.addEventListener("abort", () => {
          const err = new DOMException("Aborted", "AbortError");
          try {
            reject(err);
          } catch {
            // already settled
          }
        });
      }
      return promise;
    },
  ),
}));

import SelectedLocation from "./SelectedLocation";
import { getDistrictMonthlySeries } from "../../api/boundaries";

const mockedSeries = getDistrictMonthlySeries as unknown as ReturnType<typeof vi.fn>;

beforeEach(() => {
  pendingByKey.clear();
  mockedSeries.mockClear();
  useDistrictDataStore.setState({
    byKey: {},
    controllers: {},
    generation: {},
    inflight: {},
  });
  useAppStore.setState({
    selectedStateId: "S1",
    selectedDistrictId: null,
    selectedVariable: "precipitation",
    startMonth: "2025-01",
    endMonth: "2025-12",
    availableRange: { minYear: 2025, minMonth: 1, maxYear: 2025, maxMonth: 12 },
    states: [
      { id: "S1", name: "State One" },
      { id: "S2", name: "State Two" },
    ],
    districts: [
      { id: "D1", name: "District One" },
      { id: "D2", name: "District Two" },
      { id: "D3", name: "District Three" },
    ],
  });
});

function spinnerCount(container: HTMLElement): number {
  return container.querySelectorAll(".py-8").length;
}

function pendingCount(
  districtId: string,
  variable: string,
): number {
  return pendingByKey.get(`${districtId}|${variable}`)?.length ?? 0;
}

function latestPending(
  districtId: string,
  variable: string,
): Pending | undefined {
  const list = pendingByKey.get(`${districtId}|${variable}`) ?? [];
  return list[list.length - 1];
}

describe("SelectedLocation — canonical hook: per-district fetch count", () => {
  it("issues exactly three time-series calls per district selection (one per canonical variable), NOT six", async () => {
    useAppStore.setState({ selectedDistrictId: "D1" });
    render(<SelectedLocation />);

    await waitFor(() => {
      expect(pendingCount("D1", "precipitation")).toBe(1);
    });
    await act(async () => {
      latestPending("D1", "precipitation")?.resolve(
        defaultSeriesResponse("D1", "precipitation", [1.0, 1.0, 1.0]),
      );
    });
    await waitFor(() => {
      expect(pendingCount("D1", "soil_moisture")).toBe(1);
    });
    await act(async () => {
      latestPending("D1", "soil_moisture")?.resolve(
        defaultSeriesResponse("D1", "soil_moisture", [1.0, 1.0, 1.0]),
      );
    });
    await waitFor(() => {
      expect(pendingCount("D1", "surface_runoff")).toBe(1);
    });
    await act(async () => {
      latestPending("D1", "surface_runoff")?.resolve(
        defaultSeriesResponse("D1", "surface_runoff", [1.0, 1.0, 1.0]),
      );
    });
    await waitFor(() => {
      expect(mockedSeries).toHaveBeenCalledTimes(3);
    });

    // The legacy /statistics endpoint is NEVER called from the right
    // panel after the demand-driven refactor.
    const { getDistrictRangeStatistics } = await import("../../api/boundaries");
    expect(getDistrictRangeStatistics).not.toHaveBeenCalled();
  });
});

describe("SelectedLocation — state change clears loading", () => {
  it("clears loading immediately on state change mid-fetch and discards stale responses", async () => {
    useAppStore.setState({ selectedDistrictId: "D1" });
    const { container } = render(<SelectedLocation />);

    await waitFor(() => {
      expect(pendingCount("D1", "precipitation")).toBe(1);
    });
    expect(spinnerCount(container)).toBeGreaterThan(0);

    // User clicks state S2. The composite setter nulls selectedDistrictId.
    await act(async () => {
      useAppStore.getState().setSelectedStateId("S2");
    });

    expect(spinnerCount(container)).toBe(0);

    // Now resolve D1 pending requests. None may commit.
    await act(async () => {
      latestPending("D1", "precipitation")?.resolve(
        defaultSeriesResponse("D1", "precipitation", [1.0, 1.0, 1.0]),
      );
    });

    await new Promise((r) => setTimeout(r, 10));
    expect(spinnerCount(container)).toBe(0);
    // No KPI numbers visible (no district selected \u2192 component returns null).
  });
});

describe("SelectedLocation — D1 -> D2 -> D3 rapid district changes", () => {
  it("only the latest (D3) request commits; D1 and D2 stale responses are discarded", async () => {
    useAppStore.setState({ selectedDistrictId: "D1" });
    render(<SelectedLocation />);

    await waitFor(() => {
      expect(pendingCount("D1", "precipitation")).toBe(1);
    });

    await act(async () => {
      useAppStore.getState().setSelectedDistrictId("D2");
    });
    await waitFor(() => {
      expect(pendingCount("D2", "precipitation")).toBe(1);
    });

    await act(async () => {
      useAppStore.getState().setSelectedDistrictId("D3");
    });
    await waitFor(() => {
      expect(pendingCount("D3", "precipitation")).toBe(1);
    });

    await act(async () => {
      latestPending("D3", "precipitation")?.resolve(
        defaultSeriesResponse("D3", "precipitation", [3.0, 3.0, 3.0]),
      );
    });
    await waitFor(() => {
      expect(pendingCount("D3", "soil_moisture")).toBe(1);
    });
    await act(async () => {
      latestPending("D3", "soil_moisture")?.resolve(
        defaultSeriesResponse("D3", "soil_moisture", [3.0, 3.0, 3.0]),
      );
    });
    await waitFor(() => {
      expect(pendingCount("D3", "surface_runoff")).toBe(1);
    });
    await act(async () => {
      latestPending("D3", "surface_runoff")?.resolve(
        defaultSeriesResponse("D3", "surface_runoff", [3.0, 3.0, 3.0]),
      );
    });

    // D3 visible — displayed KPI values are converted to mm.
    await waitFor(() => {
      expect(screen.getAllByText("3000.000000").length).toBe(2);
      expect(screen.getByText("210.000000")).toBeInTheDocument();
    });

    // Now resolve D2 with mean = 2.0. MUST NOT overwrite D3.
    await act(async () => {
      latestPending("D2", "precipitation")?.resolve(
        defaultSeriesResponse("D2", "precipitation", [2.0, 2.0, 2.0]),
      );
    });
    await new Promise((r) => setTimeout(r, 10));
    expect(screen.queryByText("2000.000000")).not.toBeInTheDocument();
    expect(screen.queryByText("140.000000")).not.toBeInTheDocument();
    expect(screen.getAllByText("3000.000000").length).toBe(2);
    expect(screen.getByText("210.000000")).toBeInTheDocument();

    // And D1 with mean = 1.0. MUST NOT overwrite.
    await act(async () => {
      latestPending("D1", "precipitation")?.resolve(
        defaultSeriesResponse("D1", "precipitation", [1.0, 1.0, 1.0]),
      );
    });
    await new Promise((r) => setTimeout(r, 10));
    expect(screen.queryByText("1000.000000")).not.toBeInTheDocument();
    expect(screen.queryByText("70.000000")).not.toBeInTheDocument();
    expect(screen.getAllByText("3000.000000").length).toBe(2);
    expect(screen.getByText("210.000000")).toBeInTheDocument();

    // Loading is now false (D3 cleared it).
    expect(spinnerCount(document.body)).toBe(0);
  });
});
