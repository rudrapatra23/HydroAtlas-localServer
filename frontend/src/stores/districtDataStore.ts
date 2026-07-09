/**
 * Canonical district data store.
 *
 * Implements the demand-driven district architecture:
 *   - One canonical result per (districtId, startMonth, endMonth, variables)
 *     is fetched and cached at this layer.
 *   - Both the right-side `SelectedLocation` panel and the bottom
 *     `BottomPanel` consume from this store. They MUST NOT initiate
 *     duplicate raster computations for the same key.
 *   - Network-level in-flight deduplication for the underlying
 *     /districts/{id}/time-series endpoint is provided by
 *     `getDistrictMonthlySeries` in `api/boundaries.ts`.
 *
 * Synchronisation primitives (cross-cutting correctness contract):
 *   - One AbortController per canonical key. Bumping the per-key
 *     `generation` invalidates any earlier async continuation that is
 *     still in flight; the generation check is the correctness
 *     backstop for the case where the abort arrives after the response
 *     body has already been parsed.
 *   - `ensureLoaded` is idempotent: if a request for the same key is
 *     already loading or has already produced a result, the call is a
 *     no-op and returns the in-flight / completed promise.
 *   - Per-variable fetches are sequenced sequentially inside
 *     `ensureLoaded` so that the AbortController applies to every
 *     individual request; a second caller that arrives while the first
 *     is loading attaches to the same in-flight state via the
 *     `generation` guard rather than starting a second sequence.
 */

import { create } from "zustand";
import {
  DistrictMonthlySeries,
  MonthlySeriesPoint,
  getDistrictMonthlySeries,
} from "../api/boundaries";

export type Variable = "precipitation" | "soil_moisture" | "surface_runoff";

const DISPLAY_UNITS_MM: Record<Variable, string> = {
  precipitation: "mm",
  soil_moisture: "mm",
  surface_runoff: "mm",
};

const DISPLAY_FACTORS: Record<Variable, number> = {
  precipitation: 1000,
  // ERA5-Land ``swvl1`` is volumetric soil water for the top 0-7 cm
  // layer; convert the fraction to equivalent water depth in mm.
  soil_moisture: 70,
  surface_runoff: 1000,
};

/** Sorted-variable-set equality is required for canonical key collisions. */
export function canonicalKey(
  districtId: string,
  startMonth: string,
  endMonth: string,
  variables: readonly Variable[],
): string {
  const sortedVars = variables.slice().sort().join(",");
  return `${districtId}|${startMonth}|${endMonth}|${sortedVars}`;
}

export interface DistrictSeriesEntry {
  status: "idle" | "loading" | "ready" | "error";
  error: string | null;
  noData: boolean;
  seriesByVariable: Partial<Record<Variable, DistrictMonthlySeries>>;
  monthsProcessed: number;
}

export const EMPTY_ENTRY: DistrictSeriesEntry = {
  status: "idle",
  error: null,
  noData: false,
  seriesByVariable: {},
  monthsProcessed: 0,
};

export interface EnsureLoadedParams {
  districtId: string;
  startMonth: string;
  endMonth: string;
  variables: readonly Variable[];
}

interface DistrictDataState {
  byKey: Record<string, DistrictSeriesEntry>;
  controllers: Record<string, AbortController | null>;
  generation: Record<string, number>;
  inflight: Record<string, Promise<void> | null>;
  ensureLoaded: (params: EnsureLoadedParams) => Promise<void>;
  /** Returns the current entry for a key, or the empty entry. */
  getEntry: (key: string) => DistrictSeriesEntry;
  /** Cancels any in-flight fetch for a key. */
  cancel: (key: string) => void;
  /** Resets a key to the empty entry (used by callers when params become invalid). */
  reset: (key: string) => void;
}

export const useDistrictDataStore = create<DistrictDataState>((set, get) => ({
  byKey: {},
  controllers: {},
  generation: {},
  inflight: {},

  ensureLoaded: async (params) => {
    const key = canonicalKey(
      params.districtId,
      params.startMonth,
      params.endMonth,
      params.variables,
    );

    // If a request for the same key is already in flight, attach to it.
    const inflight = get().inflight[key];
    if (inflight) {
      return inflight;
    }
    const cur = get().byKey[key];
    if (cur && cur.status === "ready") {
      return;
    }

    // Bump generation so any earlier async continuation is invalidated.
    const gen = (get().generation[key] ?? 0) + 1;
    const ac = new AbortController();

    // The inflight promise is what other callers (including other
    // components mounting in the same frame) await.
    let resolveInflight!: () => void;
    let rejectInflight!: (e: unknown) => void;
    const inflightPromise = new Promise<void>((res, rej) => {
      resolveInflight = res;
      rejectInflight = rej;
    });

    set((s) => ({
      generation: { ...s.generation, [key]: gen },
      controllers: { ...s.controllers, [key]: ac },
      inflight: { ...s.inflight, [key]: inflightPromise },
      byKey: {
        ...s.byKey,
        [key]: {
          status: "loading",
          error: null,
          noData: false,
          seriesByVariable: {},
          monthsProcessed: 0,
        },
      },
    }));

    const runFetch = async () => {
      try {
        const seriesByVariable: Partial<Record<Variable, DistrictMonthlySeries>> = {};
        let monthsProcessed = 0;
        let anyNoData = false;

        // Validate that start <= end (defensive — caller should check too).
        const startKey =
          Number(params.startMonth.slice(0, 4)) * 12 +
          Number(params.startMonth.slice(5, 7));
        const endKey =
          Number(params.endMonth.slice(0, 4)) * 12 +
          Number(params.endMonth.slice(5, 7));
        if (startKey > endKey) {
          throw new Error("Start Month must be on or before End Month.");
        }

        for (const variable of params.variables) {
          // Generation guard: if a newer call has bumped our key, abort.
          if (get().generation[key] !== gen) return;
          if (ac.signal.aborted) return;
          const body = {
            start_year: Number(params.startMonth.slice(0, 4)),
            start_month: Number(params.startMonth.slice(5, 7)),
            end_year: Number(params.endMonth.slice(0, 4)),
            end_month: Number(params.endMonth.slice(5, 7)),
            variable,
          };
          try {
            const response = await getDistrictMonthlySeries(
              params.districtId,
              body,
              ac.signal,
            );
            // Generation guard again — the await above is the long pole.
            if (get().generation[key] !== gen) return;
            seriesByVariable[variable] = response;
            if (response.months_processed > monthsProcessed) {
              monthsProcessed = response.months_processed;
            }
          } catch (err: any) {
            if (get().generation[key] !== gen) return;
            const message =
              err instanceof Error ? err.message : String(err);
            const aborted =
              (err instanceof DOMException && err.name === "AbortError") ||
              /AbortError/i.test(message);
            if (aborted) return;
            const notFound = /404/.test(message);
            if (notFound) {
              anyNoData = true;
              // Mark only this variable as missing; continue with others.
              seriesByVariable[variable] = {
                district_id: params.districtId,
                variable,
                start_year: body.start_year,
                start_month: body.start_month,
                end_year: body.end_year,
                end_month: body.end_month,
                months_processed: 0,
                points: [],
              };
              continue;
            }
            throw err;
          }
        }

        // Final generation guard before commit.
        if (get().generation[key] !== gen) return;

        set((s) => ({
          byKey: {
            ...s.byKey,
            [key]: {
              status: "ready",
              error: null,
              noData: anyNoData,
              seriesByVariable,
              monthsProcessed,
            },
          },
        }));
        resolveInflight();
      } catch (err: any) {
        if (get().generation[key] !== gen) return;
        const message = err instanceof Error ? err.message : String(err);
        const aborted =
          (err instanceof DOMException && err.name === "AbortError") ||
          /AbortError/i.test(message);
        if (aborted) return;
        const notFound = /404/.test(message);
        set((s) => ({
          byKey: {
            ...s.byKey,
            [key]: {
              status: "error",
              error: notFound ? null : message,
              noData: notFound,
              seriesByVariable: {},
              monthsProcessed: 0,
            },
          },
        }));
        rejectInflight(err);
      } finally {
        // Only clear the controller if it's still ours.
        if (get().controllers[key] === ac) {
          set((s) => ({ controllers: { ...s.controllers, [key]: null } }));
        }
        set((s) => ({ inflight: { ...s.inflight, [key]: null } }));
      }
    };

    // Kick off without awaiting — the returned promise from this action
    // is the inflightPromise so concurrent callers can attach.
    void runFetch();
    return inflightPromise;
  },

  getEntry: (key) => get().byKey[key] ?? EMPTY_ENTRY,

  cancel: (key) => {
    const ac = get().controllers[key];
    if (ac) ac.abort();
    set((s) => ({
      controllers: { ...s.controllers, [key]: null },
      generation: { ...s.generation, [key]: (s.generation[key] ?? 0) + 1 },
    }));
  },

  reset: (key) => {
    const ac = get().controllers[key];
    if (ac) ac.abort();
    set((s) => ({
      controllers: { ...s.controllers, [key]: null },
      generation: { ...s.generation, [key]: (s.generation[key] ?? 0) + 1 },
      byKey: { ...s.byKey, [key]: EMPTY_ENTRY },
    }));
  },
}));

/**
 * Derive the canonical aggregated KPIs for a single variable from its
 * time-series response. Returns null if the series is absent or has no
 * points.
 *
 * Mathematical equivalence: these match the values previously returned
 * by the dedicated `/districts/{id}/statistics` endpoint because both
 * the legacy endpoint (`_aggregate_for_geometry`) and this derivation
 * reduce per-month `mean`/`min`/`max` over the same monthly
 * `RasterClipResult` records, which themselves reduce NaN-filtered
 * pixels equally within each raster. There is no per-pixel weighting
 * applied anywhere in the pipeline, so the equal-weight mean-of-monthly-
 * means is identical to a pixel-weighted reduction collapsed per month.
 */
export function deriveKpis(
  series: DistrictMonthlySeries | undefined,
): { mean: number; min: number; max: number; monthsProcessed: number } | null {
  if (!series || !series.points || series.points.length === 0) return null;
  let sum = 0;
  let min = Infinity;
  let max = -Infinity;
  for (const p of series.points) {
    sum += p.mean;
    if (p.min < min) min = p.min;
    if (p.max > max) max = p.max;
  }
  return {
    mean: sum / series.points.length,
    min,
    max,
    monthsProcessed: series.months_processed,
  };
}

export function getDisplayUnit(variable: Variable): string {
  return DISPLAY_UNITS_MM[variable];
}

export function toDisplayValue(variable: Variable, value: number): number {
  return value * DISPLAY_FACTORS[variable];
}

export function toDisplayPoint(
  variable: Variable,
  point: MonthlySeriesPoint,
): MonthlySeriesPoint {
  return {
    ...point,
    mean: toDisplayValue(variable, point.mean),
    min: toDisplayValue(variable, point.min),
    max: toDisplayValue(variable, point.max),
  };
}
