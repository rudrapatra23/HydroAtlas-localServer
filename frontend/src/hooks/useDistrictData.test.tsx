/**
 * Canonical-hook tests for `useDistrictData`.
 *
 * Pins the cross-cutting correctness contract of the demand-driven
 * district architecture:
 *
 *   1. SHARED FETCH \u2014 Two components mounted with the same
 *      (district, range, variables) key trigger exactly ONE network
 *      request per variable (not two). Both consumers render from the
 *      same canonical store entry.
 *   2. CACHE REUSE \u2014 A second mount after the first has resolved
 *      does NOT trigger any new network request for the same key.
 *   3. D1 \u2192 D2 \u2192 D3 RACE \u2014 Rapid key changes bump the per-key
 *      generation counter; stale responses from older keys cannot
 *      overwrite the latest entry.
 *   4. UNCHANGED KEY \u2014 Re-renders with the same key do NOT trigger
 *      any new fetch.
 *   5. KEY CHANGE \u2014 Changing the district, range, or variable set
 *      triggers exactly one fresh fetch sequence.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, act, waitFor } from "@testing-library/react";
import { useDistrictData } from "./useDistrictData";

// Hold every MonthlySeries request keyed by (districtId, variable).
type Pending = {
  promise: Promise<any>;
  resolve: (v: any) => void;
  reject: (e: any) => void;
  signal?: AbortSignal;
};
const pendingByKey = new Map<string, Pending[]>();

function pendingKey(districtId: string, variable: string): string {
  return `${districtId}|${variable}`;
}

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
    end_month: monthlyMeans.length,
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

vi.mock("../api/boundaries", () => ({
  getDistrictMonthlySeries: vi.fn(
    (districtId: string, body: any, signal?: AbortSignal) => {
      let resolve!: (v: any) => void;
      let reject!: (e: any) => void;
      const promise = new Promise<any>((res, rej) => {
        resolve = res;
        reject = rej;
      });
      const key = pendingKey(districtId, body.variable);
      const list = pendingByKey.get(key) ?? [];
      list.push({ promise, resolve, reject, signal });
      pendingByKey.set(key, list);
      if (signal) {
        signal.addEventListener("abort", () => {
          try {
            reject(new DOMException("Aborted", "AbortError"));
          } catch {
            // already settled
          }
        });
      }
      return promise;
    },
  ),
}));

import { getDistrictMonthlySeries } from "../api/boundaries";
import { useDistrictDataStore } from "../stores/districtDataStore";

const mockedSeries = getDistrictMonthlySeries as unknown as ReturnType<typeof vi.fn>;

beforeEach(() => {
  pendingByKey.clear();
  mockedSeries.mockClear();
  // Reset the canonical store between tests so prior entries do not
  // bleed across test cases.
  useDistrictDataStore.setState({
    byKey: {},
    controllers: {},
    generation: {},
    inflight: {},
  });
});

// ── Test harness: a tiny component that mounts the hook ────────────────

function Harness({
  districtId,
  startMonth,
  endMonth,
  variables,
  onSnapshot,
}: {
  districtId: string | null;
  startMonth: string | null;
  endMonth: string | null;
  variables: ReadonlyArray<"precipitation" | "soil_moisture" | "surface_runoff">;
  onSnapshot?: (snap: {
    loading: boolean;
    ready: boolean;
    noData: boolean;
    error: string | null;
    seriesByVariable: Record<string, any>;
    monthsProcessed: number;
  }) => void;
}) {
  const snap = useDistrictData({ districtId, startMonth, endMonth, variables });
  if (onSnapshot) onSnapshot(snap);
  return (
    <div>
      <span data-testid="loading">{String(snap.loading)}</span>
      <span data-testid="ready">{String(snap.ready)}</span>
      <span data-testid="noData">{String(snap.noData)}</span>
      <span data-testid="error">{snap.error ?? ""}</span>
      <span data-testid="monthsProcessed">{String(snap.monthsProcessed)}</span>
    </div>
  );
}

// ── Test 1: SHARED FETCH ────────────────────────────────────────────────

describe("useDistrictData — shared fetch across consumers", () => {
  it("two components with the same key trigger exactly ONE /time-series call per variable", async () => {
    // Render two harnesses simultaneously with the same arguments.
    render(
      <>
        <Harness
          districtId="D1"
          startMonth="2025-01"
          endMonth="2025-12"
          variables={["precipitation", "soil_moisture", "surface_runoff"]}
        />
        <Harness
          districtId="D1"
          startMonth="2025-01"
          endMonth="2025-12"
          variables={["precipitation", "soil_moisture", "surface_runoff"]}
        />
      </>,
    );

    // Wait until each (district, variable) has at least one in-flight
    // request. The key invariant: exactly one pending promise per
    // variable, NOT two.
    await waitFor(() => {
      expect(pendingByKey.get("D1|precipitation")?.length).toBe(1);
      expect(pendingByKey.get("D1|soil_moisture")?.length).toBe(1);
      expect(pendingByKey.get("D1|surface_runoff")?.length).toBe(1);
    });

    // Total network calls \u2014 one per variable, not six.
    expect(mockedSeries).toHaveBeenCalledTimes(3);
  });

  it("two components with different keys trigger independent fetches", async () => {
    render(
      <>
        <Harness
          districtId="D1"
          startMonth="2025-01"
          endMonth="2025-12"
          variables={["precipitation"]}
        />
        <Harness
          districtId="D2"
          startMonth="2025-01"
          endMonth="2025-12"
          variables={["precipitation"]}
        />
      </>,
    );

    await waitFor(() => {
      expect(pendingByKey.get("D1|precipitation")?.length).toBe(1);
      expect(pendingByKey.get("D2|precipitation")?.length).toBe(1);
    });
    expect(mockedSeries).toHaveBeenCalledTimes(2);
  });
});

// ── Test 2: CACHE REUSE ─────────────────────────────────────────────────

describe("useDistrictData — cache reuse", () => {
  it("after a key resolves, a fresh mount with the same key does NOT issue a new network call", async () => {
    const { unmount } = render(
      <Harness
        districtId="D1"
        startMonth="2025-01"
        endMonth="2025-12"
        variables={["precipitation"]}
      />,
    );

    await waitFor(() => {
      expect(pendingByKey.get("D1|precipitation")?.length).toBe(1);
    });
    expect(mockedSeries).toHaveBeenCalledTimes(1);

    // Resolve.
    await act(async () => {
      const list = pendingByKey.get("D1|precipitation") ?? [];
      for (const p of list) {
        p.resolve(defaultSeriesResponse("D1", "precipitation", [1.0, 2.0, 3.0]));
      }
    });

    await waitFor(() => {
      expect(
        useDistrictDataStore.getState().byKey[
          "D1|2025-01|2025-12|precipitation"
        ]?.status,
      ).toBe("ready");
    });

    // Unmount and remount with the same key. Cache must serve the
    // request without a new network call.
    unmount();
    expect(mockedSeries).toHaveBeenCalledTimes(1);

    render(
      <Harness
        districtId="D1"
        startMonth="2025-01"
        endMonth="2025-12"
        variables={["precipitation"]}
      />,
    );
    await act(async () => {});
    // No new network call; the already-resolved promise remains in the
    // test harness bookkeeping map.
    expect(mockedSeries).toHaveBeenCalledTimes(1);
  });
});

// ── Test 3: D1 → D2 → D3 RACE ──────────────────────────────────────────

describe("useDistrictData — D1 → D2 → D3 race", () => {
  it("stale D1/D2 responses cannot overwrite the fresh D3 entry", async () => {
    render(
      <Harness
        districtId="D1"
        startMonth="2025-01"
        endMonth="2025-12"
        variables={["precipitation"]}
      />,
    );

    await waitFor(() => {
      expect(pendingByKey.get("D1|precipitation")?.length).toBe(1);
    });

    // Switch to D2 mid-flight.
    await act(async () => {
      render(
        <Harness
          districtId="D2"
          startMonth="2025-01"
          endMonth="2025-12"
          variables={["precipitation"]}
        />,
      );
    });
    await waitFor(() => {
      expect(pendingByKey.get("D2|precipitation")?.length).toBe(1);
    });

    // Switch to D3 mid-flight.
    await act(async () => {
      render(
        <Harness
          districtId="D3"
          startMonth="2025-01"
          endMonth="2025-12"
          variables={["precipitation"]}
        />,
      );
    });
    await waitFor(() => {
      expect(pendingByKey.get("D3|precipitation")?.length).toBe(1);
    });

    // Resolve D3 first.
    await act(async () => {
      const list = pendingByKey.get("D3|precipitation") ?? [];
      for (const p of list) {
        p.resolve(defaultSeriesResponse("D3", "precipitation", [3.0, 3.0, 3.0]));
      }
    });

    await waitFor(() => {
      const entry = useDistrictDataStore.getState().byKey[
        "D3|2025-01|2025-12|precipitation"
      ];
      expect(entry?.status).toBe("ready");
    });

    // Now resolve D2 stale. Must NOT overwrite D3.
    await act(async () => {
      const list = pendingByKey.get("D2|precipitation") ?? [];
      for (const p of list) {
        p.resolve(defaultSeriesResponse("D2", "precipitation", [2.0, 2.0, 2.0]));
      }
    });
    await new Promise((r) => setTimeout(r, 10));
    const entry3 = useDistrictDataStore.getState().byKey[
      "D3|2025-01|2025-12|precipitation"
    ];
    expect(entry3?.status).toBe("ready");
    expect(entry3?.seriesByVariable.precipitation?.points[0]?.mean).toBe(3.0);

    // Resolve D1 stale. Must NOT overwrite.
    await act(async () => {
      const list = pendingByKey.get("D1|precipitation") ?? [];
      for (const p of list) {
        p.resolve(defaultSeriesResponse("D1", "precipitation", [1.0, 1.0, 1.0]));
      }
    });
    await new Promise((r) => setTimeout(r, 10));
    const entry3Again = useDistrictDataStore.getState().byKey[
      "D3|2025-01|2025-12|precipitation"
    ];
    expect(entry3Again?.seriesByVariable.precipitation?.points[0]?.mean).toBe(3.0);
  });

  it("cancels the old district request when selection is cleared by a state change", async () => {
    const { rerender } = render(
      <Harness
        districtId="D1"
        startMonth="2025-01"
        endMonth="2025-12"
        variables={["precipitation"]}
      />,
    );

    await waitFor(() => {
      expect(pendingByKey.get("D1|precipitation")?.length).toBe(1);
    });

    const request = pendingByKey.get("D1|precipitation")![0];
    expect(request.signal?.aborted).toBe(false);

    rerender(
      <Harness
        districtId={null}
        startMonth="2025-01"
        endMonth="2025-12"
        variables={["precipitation"]}
      />,
    );

    await waitFor(() => {
      expect(request.signal?.aborted).toBe(true);
    });

    const entry = useDistrictDataStore.getState().byKey[
      "D1|2025-01|2025-12|precipitation"
    ];
    expect(entry?.status).toBe("loading");
  });
});

// ── Test 4: UNCHANGED KEY ──────────────────────────────────────────────

describe("useDistrictData — unchanged key does not recompute", () => {
  it("re-renders with the same key do NOT trigger new network calls", async () => {
    let renderCount = 0;
    function CountingHarness() {
      renderCount += 1;
      useDistrictData({
        districtId: "D1",
        startMonth: "2025-01",
        endMonth: "2025-12",
        variables: ["precipitation"],
      });
      return null;
    }

    const { rerender } = render(<CountingHarness />);
    await waitFor(() => {
      expect(pendingByKey.get("D1|precipitation")?.length).toBe(1);
    });
    expect(mockedSeries).toHaveBeenCalledTimes(1);

    // Force several re-renders with the same key.
    rerender(<CountingHarness />);
    rerender(<CountingHarness />);
    rerender(<CountingHarness />);

    expect(renderCount).toBeGreaterThan(1);
    expect(mockedSeries).toHaveBeenCalledTimes(1);
  });
});

// ── Test 5: KEY CHANGE ─────────────────────────────────────────────────

describe("useDistrictData — key change triggers exactly one fresh fetch", () => {
  it("changing the range triggers exactly one new fetch per variable", async () => {
    const { rerender } = render(
      <Harness
        districtId="D1"
        startMonth="2025-01"
        endMonth="2025-12"
        variables={["precipitation", "soil_moisture"]}
      />,
    );

    await waitFor(() => {
      expect(pendingByKey.get("D1|precipitation")?.length).toBe(1);
      expect(pendingByKey.get("D1|soil_moisture")?.length).toBe(1);
    });
    expect(mockedSeries).toHaveBeenCalledTimes(2);

    // Resolve the first key.
    await act(async () => {
      for (const variable of ["precipitation", "soil_moisture"]) {
        const list = pendingByKey.get(`D1|${variable}`) ?? [];
        for (const p of list) {
          p.resolve(defaultSeriesResponse("D1", variable, [1.0]));
        }
      }
    });

    await waitFor(() => {
      expect(
        useDistrictDataStore.getState().byKey[
          "D1|2025-01|2025-12|precipitation,soil_moisture"
        ]?.status,
      ).toBe("ready");
    });

    // Change the range.
    rerender(
      <Harness
        districtId="D1"
        startMonth="2025-02"
        endMonth="2025-06"
        variables={["precipitation", "soil_moisture"]}
      />,
    );

    // Exactly one new fetch per variable for the new key. The test
    // bookkeeping map still contains the old resolved requests.
    await waitFor(() => {
      expect(pendingByKey.get("D1|precipitation")?.length).toBe(2);
      expect(pendingByKey.get("D1|soil_moisture")?.length).toBe(2);
    });
    // Total network calls: 2 (initial) + 2 (new range) = 4.
    expect(mockedSeries).toHaveBeenCalledTimes(4);
  });
});
