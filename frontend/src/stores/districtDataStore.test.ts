/**
 * Scientific equivalence tests for the canonical district-data
 * pipeline.
 *
 * The previous architecture called two independent endpoints per
 * district selection:
 *   - POST /districts/{id}/statistics      \u2192 aggregated mean/min/max
 *   - POST /districts/{id}/time-series    \u2192 per-month points
 *
 * Both walked the same `_aggregate_for_geometry` per-month clip loop.
 * The aggregated statistics endpoint reduced each month\u2019s
 * `RasterClipResult` (mean, min, max) via:
 *   range_mean = np.mean(per_month_means)
 *   range_min  = np.min(per_month_mins)
 *   range_max  = np.max(per_month_maxes)
 *
 * These tests pin the equivalence:
 *   mean = deriveKpis(time_series).mean      === np.mean([p.mean for p in points])
 *   min  = deriveKpis(time_series).min       === np.min([p.min for p in points])
 *   max  = deriveKpis(time_series).max       === np.max([p.max for p in points])
 *
 * Edge cases verified:
 *   - Months with zero valid pixels contribute (0, 0, 0).
 *   - NaN-equivalent zeros do not silently flip the aggregation.
 *   - Single-month range still produces a valid derived KPI.
 *   - Empty / missing series returns null (no crash, no NaN).
 *   - Negative values (e.g. soil moisture deficit) are handled.
 */

import { describe, it, expect } from "vitest";
import {
  canonicalKey,
  deriveKpis,
  getDisplayUnit,
  toDisplayPoint,
  toDisplayValue,
  type Variable,
} from "./districtDataStore";

describe("districtDataStore \u2014 scientific equivalence: deriveKpis vs /statistics", () => {
  it("derives mean/min/max identical to np.mean/np.min/np.max of monthly values", () => {
    const points = [
      { year: 2025, month: 1, mean: 10.0, min: 5.0, max: 15.0 },
      { year: 2025, month: 2, mean: 20.0, min: 12.0, max: 28.0 },
      { year: 2025, month: 3, mean: 30.0, min: 18.0, max: 42.0 },
    ];
    const series = {
      district_id: "D1",
      variable: "precipitation",
      start_year: 2025,
      start_month: 1,
      end_year: 2025,
      end_month: 3,
      months_processed: 3,
      points,
    };
    const derived = deriveKpis(series);
    expect(derived).not.toBeNull();
    // Equal-weight mean of monthly means.
    expect(derived!.mean).toBeCloseTo((10 + 20 + 30) / 3, 12);
    // min of monthly mins \u2014 same as np.min(per_month_mins) in legacy path.
    expect(derived!.min).toBeCloseTo(5.0, 12);
    // max of monthly maxes \u2014 same as np.max(per_month_maxes) in legacy path.
    expect(derived!.max).toBeCloseTo(42.0, 12);
    expect(derived!.monthsProcessed).toBe(3);
  });

  it("months with zero valid pixels contribute (0, 0, 0) exactly as the legacy endpoint would", () => {
    // In `_compute_stats_for_geometry`, valid_pixel_count == 0 falls
    // through to ``mean=0, min=0, max=0`` (raster_computation.py).
    // The monthly series carries those zeros; the derived KPI reduces
    // them with the SAME equal-weight semantics as the legacy endpoint.
    const points = [
      { year: 2025, month: 1, mean: 10.0, min: 5.0, max: 15.0 },
      { year: 2025, month: 2, mean: 0.0, min: 0.0, max: 0.0 },
      { year: 2025, month: 3, mean: 20.0, min: 12.0, max: 28.0 },
    ];
    const series = {
      district_id: "D1",
      variable: "precipitation",
      start_year: 2025,
      start_month: 1,
      end_year: 2025,
      end_month: 3,
      months_processed: 3,
      points,
    };
    const derived = deriveKpis(series);
    expect(derived!.mean).toBeCloseTo((10 + 0 + 20) / 3, 12);
    expect(derived!.min).toBeCloseTo(0.0, 12);
    expect(derived!.max).toBeCloseTo(28.0, 12);
  });

  it("handles a single-month range", () => {
    const series = {
      district_id: "D1",
      variable: "precipitation",
      start_year: 2025,
      start_month: 6,
      end_year: 2025,
      end_month: 6,
      months_processed: 1,
      points: [{ year: 2025, month: 6, mean: 7.5, min: 3.0, max: 12.0 }],
    };
    const derived = deriveKpis(series);
    expect(derived!.mean).toBeCloseTo(7.5, 12);
    expect(derived!.min).toBeCloseTo(3.0, 12);
    expect(derived!.max).toBeCloseTo(12.0, 12);
  });

  it("handles negative values (soil moisture deficit scenarios)", () => {
    const points = [
      { year: 2025, month: 1, mean: -2.0, min: -5.0, max: 0.5 },
      { year: 2025, month: 2, mean: -1.0, min: -3.0, max: 1.0 },
    ];
    const series = {
      district_id: "D1",
      variable: "soil_moisture",
      start_year: 2025,
      start_month: 1,
      end_year: 2025,
      end_month: 2,
      months_processed: 2,
      points,
    };
    const derived = deriveKpis(series);
    expect(derived!.mean).toBeCloseTo(-1.5, 12);
    expect(derived!.min).toBeCloseTo(-5.0, 12);
    expect(derived!.max).toBeCloseTo(1.0, 12);
  });

  it("returns null for missing or empty series", () => {
    expect(deriveKpis(undefined)).toBeNull();
    expect(
      deriveKpis({
        district_id: "D1",
        variable: "precipitation",
        start_year: 2025,
        start_month: 1,
        end_year: 2025,
        end_month: 12,
        months_processed: 0,
        points: [],
      }),
    ).toBeNull();
  });

  it("derivation matches manual equal-weight-of-monthly-means computation bit-for-bit", () => {
    // Sanity check: deriveKpis and a manual equal-weight reduction must
    // agree for a realistic 12-month synthetic series.
    const monthlyMeans = [
      3.14, 2.71, 1.41, 0.57, 4.20, 3.33, 2.22, 1.61, 5.55, 6.66, 7.77, 8.88,
    ];
    const monthlyMins = monthlyMeans.map((m) => m - 0.1);
    const monthlyMaxes = monthlyMeans.map((m) => m + 0.1);
    const points = monthlyMeans.map((m, i) => ({
      year: 2025,
      month: i + 1,
      mean: m,
      min: monthlyMins[i],
      max: monthlyMaxes[i],
    }));
    const series = {
      district_id: "D1",
      variable: "precipitation",
      start_year: 2025,
      start_month: 1,
      end_year: 2025,
      end_month: 12,
      months_processed: 12,
      points,
    };
    const derived = deriveKpis(series);
    const manualMean = monthlyMeans.reduce((a, b) => a + b, 0) / monthlyMeans.length;
    const manualMin = Math.min(...monthlyMins);
    const manualMax = Math.max(...monthlyMaxes);
    expect(derived!.mean).toBe(manualMean);
    expect(derived!.min).toBe(manualMin);
    expect(derived!.max).toBe(manualMax);
  });
});

describe("districtDataStore \u2014 canonical key composition", () => {
  it("produces the same key regardless of variable order", () => {
    const vars1: Variable[] = ["precipitation", "soil_moisture", "surface_runoff"];
    const vars2: Variable[] = ["surface_runoff", "precipitation", "soil_moisture"];
    expect(
      canonicalKey("D1", "2025-01", "2025-12", vars1),
    ).toBe(canonicalKey("D1", "2025-01", "2025-12", vars2));
  });

  it("produces different keys for different districts, ranges, or variable sets", () => {
    const base: Variable[] = ["precipitation"];
    expect(canonicalKey("D1", "2025-01", "2025-12", base)).not.toBe(
      canonicalKey("D2", "2025-01", "2025-12", base),
    );
    expect(canonicalKey("D1", "2025-01", "2025-12", base)).not.toBe(
      canonicalKey("D1", "2025-02", "2025-12", base),
    );
    expect(canonicalKey("D1", "2025-01", "2025-12", base)).not.toBe(
      canonicalKey("D1", "2025-01", "2025-11", base),
    );
    expect(
      canonicalKey("D1", "2025-01", "2025-12", ["precipitation"]),
    ).not.toBe(canonicalKey("D1", "2025-01", "2025-12", ["soil_moisture"]));
  });
});

describe("districtDataStore — display-unit conversions", () => {
  it("converts precipitation and surface runoff from meters to millimeters", () => {
    expect(getDisplayUnit("precipitation")).toBe("mm");
    expect(getDisplayUnit("surface_runoff")).toBe("mm");
    expect(toDisplayValue("precipitation", 1.25)).toBeCloseTo(1250, 12);
    expect(toDisplayValue("surface_runoff", 0.012)).toBeCloseTo(12, 12);
  });

  it("converts swvl1 soil moisture to equivalent millimeters over the 0-7 cm layer", () => {
    expect(getDisplayUnit("soil_moisture")).toBe("mm");
    expect(toDisplayValue("soil_moisture", 0.2)).toBeCloseTo(14, 12);
    expect(
      toDisplayPoint("soil_moisture", {
        year: 2025,
        month: 1,
        mean: 0.2,
        min: 0.1,
        max: 0.25,
      }),
    ).toEqual({
      year: 2025,
      month: 1,
      mean: 14,
      min: 7,
      max: 17.5,
    });
  });
});
