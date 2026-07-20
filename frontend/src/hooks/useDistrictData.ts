import { useEffect, useMemo } from "react";
import {
  useDistrictDataStore,
  canonicalKey,
  EMPTY_ENTRY,
  Variable,
  DistrictSeriesEntry,
} from "../stores/districtDataStore";

export interface UseDistrictDataParams {
  districtId: string | null;
  startMonth: string | null;
  endMonth: string | null;
  variables: readonly Variable[];
}

export interface UseDistrictDataResult {
  loading: boolean;
  ready: boolean;
  noData: boolean;
  error: string | null;
  seriesByVariable: DistrictSeriesEntry["seriesByVariable"];
  monthsProcessed: number;
}

/**
 * Thin reactive wrapper around `useDistrictDataStore`.
 *
 * All the actual fetch/dedup/cache/race-safety logic lives in the store
 * (see districtDataStore.ts). This hook's only jobs are:
 *   1. Compute the canonical key for the current params.
 *   2. Kick off `ensureLoaded` when that key changes (idempotent — the
 *      store itself is a no-op if the key is already loading/ready).
 *   3. Subscribe to the store entry for that key so the component
 *      re-renders as status/data change.
 *
 * Returns a safe default (EMPTY_ENTRY-derived) when required params are
 * missing, so consumers never read off `undefined`.
 */
export function useDistrictData(params: UseDistrictDataParams): UseDistrictDataResult {
  const { districtId, startMonth, endMonth, variables } = params;

  const isValid = Boolean(districtId && startMonth && endMonth && variables.length > 0);

  // Recomputed whenever inputs change; the resulting *string* is stable
  // in value even if `variables` is a fresh array literal each render
  // (e.g. inline arrays in JSX), so effects keyed on `key` below won't
  // re-fire on every render — only when the key's actual content does.
  const key = useMemo(() => {
    if (!districtId || !startMonth || !endMonth || variables.length === 0) return null;
    return canonicalKey(districtId, startMonth, endMonth, variables);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [districtId, startMonth, endMonth, variables.join(",")]);

  const ensureLoaded = useDistrictDataStore((s) => s.ensureLoaded);
  const cancel = useDistrictDataStore((s) => s.cancel);
  const entry = useDistrictDataStore((s) => (key ? s.byKey[key] ?? EMPTY_ENTRY : EMPTY_ENTRY));

  useEffect(() => {
    if (!key || !districtId || !startMonth || !endMonth) return;
    void ensureLoaded({ districtId, startMonth, endMonth, variables });
    return () => {
      cancel(key);
    };
    // `key` alone captures every input that matters (district, range,
    // sorted variable set); re-running only when it changes is correct
    // and avoids re-fetching on unstable array references.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  return {
    loading: isValid && (entry.status === "idle" || entry.status === "loading"),
    ready: entry.status === "ready",
    noData: entry.noData,
    error: entry.error,
    seriesByVariable: entry.seriesByVariable,
    monthsProcessed: entry.monthsProcessed,
  };
}

export default useDistrictData;
