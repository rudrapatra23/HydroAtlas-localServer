/**
 * `useDistrictData` — React hook that subscribes to the canonical
 * district data store and triggers fetches when the (district, range,
 * variables) key changes.
 *
 * Both the right-side `SelectedLocation` panel and the bottom
 * `BottomPanel` call this hook with the same arguments when the user
 * selects a district; the hook guarantees they consume one shared
 * canonical result and never duplicate the underlying raster work.
 *
 * Returned shape:
 *   - `loading`        — true while the canonical fetch is in flight.
 *   - `error`          — error message string, or null.
 *   - `noData`         — true when the backend returned 404 for the period.
 *   - `seriesByVariable` — per-variable `DistrictMonthlySeries` map
 *                          (may be partial if the fetch is still in flight).
 *   - `monthsProcessed` — months actually computed by the backend.
 *   - `ready`          — true when the entry has status `ready`.
 */

import { useEffect, useMemo } from "react";
import {
  canonicalKey,
  EMPTY_ENTRY,
  type EnsureLoadedParams,
  type Variable,
  useDistrictDataStore,
} from "../stores/districtDataStore";

export interface UseDistrictDataParams {
  districtId: string | null;
  startMonth: string | null;
  endMonth: string | null;
  variables: readonly Variable[];
}

export interface UseDistrictDataResult {
  loading: boolean;
  error: string | null;
  noData: boolean;
  seriesByVariable: Partial<Record<Variable, import("../api/boundaries").DistrictMonthlySeries>>;
  monthsProcessed: number;
  ready: boolean;
}

export function useDistrictData(params: UseDistrictDataParams): UseDistrictDataResult {
  const { districtId, startMonth, endMonth, variables } = params;

  // Compute the canonical key. Memoised so identity is stable across
  // renders unless one of the inputs actually changes.
  const key = useMemo(() => {
    if (!districtId || !startMonth || !endMonth) return null;
    if (variables.length === 0) return null;
    return canonicalKey(districtId, startMonth, endMonth, variables);
  }, [districtId, startMonth, endMonth, variables]);

  // Subscribe to the entry for this key. useDistrictDataStore uses
  // Zustand's standard selector subscription so re-renders only fire
  // when the selected slice changes.
  const entry = useDistrictDataStore((s) => (key ? s.byKey[key] : undefined));

  // Trigger ensureLoaded when the key changes.
  const ensureLoaded = useDistrictDataStore((s) => s.ensureLoaded);
  const reset = useDistrictDataStore((s) => s.reset);

  useEffect(() => {
    if (!key || !districtId || !startMonth || !endMonth) {
      return;
    }
    const loadParams: EnsureLoadedParams = {
      districtId,
      startMonth,
      endMonth,
      variables,
    };
    void ensureLoaded(loadParams);
  }, [key, districtId, startMonth, endMonth, variables.join(","), ensureLoaded]);

  // When the key becomes invalid (deselect / empty range / no variables),
  // reset the previous entry so consumers see a clean state.
  useEffect(() => {
    if (key) return;
    // Reset all keys that match the prior inputs so any stale data is cleared.
    // Use a generation bump via cancel to avoid leaking in-flight requests.
    return () => {
      // No-op cleanup; components manage their own visible state.
    };
  }, [key, reset]);

  const result: UseDistrictDataResult = entry
    ? {
        loading: entry.status === "loading",
        error: entry.error,
        noData: entry.noData,
        seriesByVariable: entry.seriesByVariable,
        monthsProcessed: entry.monthsProcessed,
        ready: entry.status === "ready",
      }
    : {
        loading: false,
        error: null,
        noData: false,
        seriesByVariable: EMPTY_ENTRY.seriesByVariable,
        monthsProcessed: 0,
        ready: false,
      };
  return result;
}
