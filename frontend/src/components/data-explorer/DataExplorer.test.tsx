import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, act, waitFor, fireEvent, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { useAppStore } from "../../stores/useAppStore";

// In-memory controllable promises per state id.
type Pending = {
  promise: Promise<any>;
  resolve: (value: any) => void;
  reject: (error: any) => void;
};
const pendingByState = new Map<string, Pending>();

vi.mock("../../api/boundaries", () => ({
  getStates: vi.fn().mockResolvedValue([]),
  getDatasets: vi.fn().mockResolvedValue([]),
  getDistricts: vi.fn((stateId: string, _signal?: AbortSignal) => {
    let resolve!: (v: any) => void;
    let reject!: (e: any) => void;
    const promise = new Promise<any>((res, rej) => {
      resolve = res;
      reject = rej;
    });
    pendingByState.set(stateId, { promise, resolve, reject });
    // If the test attaches a signal, abort should reject.
    if (_signal) {
      _signal.addEventListener("abort", () => {
        const e = new DOMException("Aborted", "AbortError");
        // Only reject if still pending.
        if (pendingByState.get(stateId)?.promise === promise) {
          reject(e);
        }
      });
    }
    return promise;
  }),
  getDistrictsGeojson: vi.fn().mockResolvedValue({ type: "FeatureCollection", features: [] }),
  getDistrictRangeStatistics: vi.fn(),
  getStateDistrictRangeStatistics: vi.fn(),
  getDistrictMonthlySeries: vi.fn(),
}));

import DataExplorer from "./DataExplorer";
import { getDistricts } from "../../api/boundaries";

const mockedGetDistricts = getDistricts as unknown as ReturnType<typeof vi.fn>;

function renderExplorer() {
  return render(
    <MemoryRouter>
      <DataExplorer />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  pendingByState.clear();
  mockedGetDistricts.mockClear();
  useAppStore.setState({
    selectedStateId: null,
    selectedDistrictId: null,
    selectedVariable: "precipitation",
    startMonth: "",
    endMonth: "",
    availableRange: null,
    states: [
      { id: "S1", name: "State One" },
      { id: "S2", name: "State Two" },
      { id: "S3", name: "State Three" },
    ],
    districts: [],
  });
});

describe("DataExplorer — H1.b district-fetch race fix", () => {
  it("commits only the latest selected state, even if S1 resolves after S2", async () => {
    renderExplorer();

    // Start with S1 (slow).
    await act(async () => {
      useAppStore.getState().setSelectedStateId("S1");
    });
    // The fetch for S1 must have been issued.
    expect(pendingByState.has("S1")).toBe(true);

    // Switch to S2 before S1 resolves.
    await act(async () => {
      useAppStore.getState().setSelectedStateId("S2");
    });
    expect(pendingByState.has("S2")).toBe(true);

    // S2 resolves fast with its districts.
    await act(async () => {
      pendingByState.get("S2")!.resolve([
        { district_id: "S2-D1", name: "S2 District One" },
        { district_id: "S2-D2", name: "S2 District Two" },
      ]);
    });

    await waitFor(() => {
      expect(useAppStore.getState().districts).toEqual([
        { id: "S2-D1", name: "S2 District One" },
        { id: "S2-D2", name: "S2 District Two" },
      ]);
    });

    // Now S1 resolves late. This MUST NOT overwrite the dropdown.
    await act(async () => {
      pendingByState.get("S1")!.resolve([
        { district_id: "S1-D1", name: "S1 District One" },
      ]);
    });

    // Give the (now stale) promise a chance to commit if it could.
    await new Promise((r) => setTimeout(r, 10));

    expect(useAppStore.getState().districts).toEqual([
      { id: "S2-D1", name: "S2 District One" },
      { id: "S2-D2", name: "S2 District Two" },
    ]);
  });

  it("aborts the previous fetch when the state changes", async () => {
    const abortSignals: AbortSignal[] = [];

    // Wrap the mock to capture signals.
    mockedGetDistricts.mockImplementation((stateId: string, signal?: AbortSignal) => {
      let resolve!: (v: any) => void;
      let reject!: (e: any) => void;
      const promise = new Promise<any>((res, rej) => {
        resolve = res;
        reject = rej;
      });
      pendingByState.set(stateId, { promise, resolve, reject });
      if (signal) abortSignals.push(signal);
      return promise;
    });

    renderExplorer();

    await act(async () => {
      useAppStore.getState().setSelectedStateId("S1");
    });
    await act(async () => {
      useAppStore.getState().setSelectedStateId("S2");
    });
    await act(async () => {
      useAppStore.getState().setSelectedStateId("S3");
    });

    // S1's and S2's signals must be aborted by the time S3 was issued.
    // The exact assertion is that at least the S1 signal was aborted;
    // aborting happens synchronously in the cleanup.
    await waitFor(() => {
      const s1Signal = abortSignals[0];
      expect(s1Signal.aborted).toBe(true);
    });
    expect(abortSignals.length).toBeGreaterThanOrEqual(3);
  });

  it("sets the active raster variable when a layer row is selected", async () => {
    renderExplorer();

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /soil moisture/i }));
    });

    expect(useAppStore.getState().selectedVariable).toBe("soil_moisture");
  });
});
