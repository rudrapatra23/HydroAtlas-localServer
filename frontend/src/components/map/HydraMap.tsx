import { useEffect, useMemo, useRef, useState } from "react";
import maplibregl, {
  Map as MapLibreMap,
  StyleSpecification,
} from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { useAppStore, monthStringToYearMonth } from "../../stores/useAppStore";
import { getDistrictsGeojson, getStateDistrictRangeStatistics } from "../../api/boundaries";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

const lightBasemapStyle: StyleSpecification = {
  version: 8,
  sources: {
    cartoLight: {
      type: "raster",
      tiles: [
        "https://a.basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png",
        "https://b.basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png",
        "https://c.basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png",
        "https://d.basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png",
      ],
      tileSize: 256,
      attribution:
        '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>',
    },
  },
  layers: [
    {
      id: "carto-light-layer",
      type: "raster",
      source: "cartoLight",
      minzoom: 0,
      maxzoom: 20,
    },
  ],
};

interface LegendThresholds {
  p5: number;
  p25: number;
  p50: number;
  p75: number;
  p95: number;
}

function HydraMap() {
  const mapContainerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<MapLibreMap | null>(null);
  const selectedStateId = useAppStore((state) => state.selectedStateId);
  const selectedDistrictId = useAppStore((state) => state.selectedDistrictId);
  const selectedVariable = useAppStore((state) => state.selectedVariable);
  const startMonth = useAppStore((state) => state.startMonth);
  const endMonth = useAppStore((state) => state.endMonth);
  const setSelectedStateId = useAppStore((state) => state.setSelectedStateId);
  const setSelectedDistrictId = useAppStore((state) => state.setSelectedDistrictId);
  const districtGeojsonRef = useRef<any | null>(null);
  const geojsonLoadedForStateRef = useRef<string | null>(null);
  // Mirrors selectedStateId so the map-init effect (which runs exactly
  // once and is intentionally NOT torn down on state changes — see
  // H1.a in .kimchi/docs/race-diagnosis.md) can read the current value
  // inside its click handler without being re-subscribed.
  const selectedStateIdRef = useRef<string | null>(selectedStateId);
  useEffect(() => {
    selectedStateIdRef.current = selectedStateId;
  }, [selectedStateId]);

  const [legendThresholds, setLegendThresholds] = useState<LegendThresholds | null>(null);
  // Tracks whether the map currently has a committed choropleth on the
  // source. Used to gate the non-destructive render contract: when a
  // fetch is in flight and we already have a choropleth, the previous
  // visualization is kept visible and an "Updating…" badge is overlaid.
  const [hasChoropleth, setHasChoropleth] = useState(false);
  const [mapLoading, setMapLoading] = useState(false);

  const emptyFeatureCollection = useMemo(
    () => ({ type: "FeatureCollection", features: [] as any[] }),
    []
  );

  async function fetchStateDistrictStatistics(
    stateId: string,
    variable: string,
    startYear: number,
    startMonth: number,
    endYear: number,
    endMonth: number,
  ) {
    return getStateDistrictRangeStatistics(stateId, {
      start_year: startYear,
      start_month: startMonth,
      end_year: endYear,
      end_month: endMonth,
      variable,
    });
  }

  function applyChoropleth(
    source: any,
    baseGeojson: any,
    stats: { districts: Array<{ district_id: string; mean: number }> }
  ) {
    const byDistrictId = new Map<string, number>();
    for (const item of stats.districts) {
      if (typeof item.mean === "number" && !Number.isNaN(item.mean)) {
        byDistrictId.set(item.district_id, item.mean);
      }
    }

    const means = Array.from(byDistrictId.values());
    if (means.length === 0) {
      setLegendThresholds(null);
      source.setData(baseGeojson);
      return;
    }

    const sorted = [...means].sort((a, b) => a - b);
    const getPercentile = (p: number) => {
      const index = (sorted.length - 1) * p;
      const lower = Math.floor(index);
      const upper = Math.ceil(index);
      if (lower === upper) return sorted[lower];
      return sorted[lower] + (sorted[upper] - sorted[lower]) * (index - lower);
    };

    const p5 = getPercentile(0.05);
    const p25 = getPercentile(0.25);
    const p50 = getPercentile(0.50);
    const p75 = getPercentile(0.75);
    const p95 = getPercentile(0.95);

    setLegendThresholds({ p5, p25, p50, p75, p95 });

    const joined = {
      ...baseGeojson,
      features: (baseGeojson.features ?? []).map((f: any) => {
        const districtId = f?.properties?.district_id as string | undefined;
        if (!districtId) return f;
        const mean = byDistrictId.get(districtId);
        if (mean === undefined) {
          return {
            ...f,
            properties: { ...f.properties, mean: null, norm: null },
          };
        }

        let norm = 0;
        if (p95 === p5) {
          norm = 0.5;
        } else if (mean <= p5) {
          norm = 0;
        } else if (mean >= p95) {
          norm = 1;
        } else if (mean <= p25) {
          norm = 0.0 + 0.25 * ((mean - p5) / (p25 - p5 || 1));
        } else if (mean <= p50) {
          norm = 0.25 + 0.25 * ((mean - p25) / (p50 - p25 || 1));
        } else if (mean <= p75) {
          norm = 0.5 + 0.25 * ((mean - p50) / (p75 - p50 || 1));
        } else {
          norm = 0.75 + 0.25 * ((mean - p75) / (p95 - p75 || 1));
        }

        return {
          ...f,
          properties: { ...f.properties, mean, norm },
        };
      }),
    };

    source.setData(joined);
  }

  useEffect(() => {
    if (!mapContainerRef.current || mapRef.current) return;

    const map = new maplibregl.Map({
      container: mapContainerRef.current,
      style: lightBasemapStyle,
      minZoom: 3.2,
      maxZoom: 12,
      attributionControl: false,
      dragRotate: false,
      touchPitch: false,
      renderWorldCopies: false,
    });

    mapRef.current = map;

    map.on("load", () => {
      map.resize();
      map.fitBounds(
        [
          [67.5, 6],
          [97.5, 37.5],
        ],
        {
          padding: { top: 50, bottom: 50, left: 50, right: 50 },
          duration: 0,
        }
      );

      map.addSource("districts", {
        type: "geojson",
        data: emptyFeatureCollection as any,
      });

      // Updated: Sequential Blues scale from White (0.0) to Dark Blue (1.0)
      map.addLayer({
        id: "districts-fill",
        type: "fill",
        source: "districts",
        paint: {
          "fill-color": [
            "case",
            ["has", "norm"],
            [
              "interpolate",
              ["linear"],
              ["get", "norm"],
              0.0, "#F7FBFF",
              0.25, "#C6DBEF",
              0.5, "#6BAED6",
              0.75, "#2171B5",
              1.0, "#08306B",
            ],
            "#F7FBFF",
          ],
          "fill-opacity": ["case", ["has", "norm"], 0.85, 0.05],
        },
      });

      // Updated: Darker border color so white/light-blue districts stay visible
      map.addLayer({
        id: "districts-line",
        type: "line",
        source: "districts",
        paint: {
          "line-color": "#1E293B",
          "line-width": 0.5,
          "line-opacity": 0.25,
        },
      });

      map.addLayer({
        id: "districts-selected-fill",
        type: "fill",
        source: "districts",
        filter: ["==", ["get", "district_id"], ""],
        paint: {
          "fill-color": "#FFFFFF",
          "fill-opacity": 0.1,
        },
      });

      map.addLayer({
        id: "districts-selected-line",
        type: "line",
        source: "districts",
        filter: ["==", ["get", "district_id"], ""],
        paint: {
          "line-color": "#0F172A",
          "line-width": 2.2,
          "line-opacity": 1,
        },
      });
    });

    map.on("click", (event) => {
      const features = map.queryRenderedFeatures(event.point, {
        layers: ["districts-fill"],
      });
      const feature = features[0];
      const districtId = feature?.properties?.district_id as string | undefined;
      const stateId = feature?.properties?.state_id as string | undefined;
      if (districtId && stateId) {
        // Read the latest selectedStateId from the ref so this handler
        // does not need the map-init effect to be re-subscribed on
        // every state change.
        if (selectedStateIdRef.current !== stateId) {
          setSelectedStateId(stateId);
          queueMicrotask(() => setSelectedDistrictId(districtId));
        } else {
          setSelectedDistrictId(districtId);
        }
      }
    });

    return () => {
      map.remove();
      mapRef.current = null;
    };
    // Intentionally NOT depending on selectedStateId: the map instance
    // is a long-lived resource and must NOT be torn down on every
    // state change. The click handler reads selectedStateId via
    // selectedStateIdRef, which is kept in sync by the small effect
    // above. See H1.a in .kimchi/docs/race-diagnosis.md.
  }, [emptyFeatureCollection, setSelectedDistrictId, setSelectedStateId]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;

    let cancelled = false;

    // Single source of truth for the non-destructive render contract:
    // the closure-scoped `cancelled` flag ensures that a stale in-flight
    // request can never overwrite a fresher one. All geometry and
    // choropleth mutations happen inside `run()` so the cleanup of the
    // previous effect run can atomically suppress its commits.
    setMapLoading(true);

    async function run(mapInstance: MapLibreMap) {
      const source = mapInstance.getSource("districts") as any;
      if (!source) {
        if (!cancelled) setMapLoading(false);
        return;
      }

      // Deselection: clear everything immediately, no fetch issued.
      if (!selectedStateId) {
        if (cancelled) return;
        districtGeojsonRef.current = emptyFeatureCollection;
        geojsonLoadedForStateRef.current = null;
        setLegendThresholds(null);
        source.setData(emptyFeatureCollection as any);
        setHasChoropleth(false);
        setMapLoading(false);
        return;
      }

      // Distinguish a state change from a variable / month / year change.
      // The previous choropleth is meaningful only when the state geometry
      // is unchanged; on a state change the old mean/norm values belong to
      // a different set of districts and must be cleared immediately so we
      // never display them on the wrong geometry.
      const stateChanged =
        geojsonLoadedForStateRef.current !== selectedStateId;

      if (stateChanged) {
        source.setData(emptyFeatureCollection as any);
        setLegendThresholds(null);
        setHasChoropleth(false);
      }

      let baseGeojson = districtGeojsonRef.current;
      if (stateChanged || !baseGeojson) {
        try {
          const geojson = await getDistrictsGeojson(selectedStateId);
          if (cancelled) return;
          baseGeojson = geojson as any;
          districtGeojsonRef.current = baseGeojson;
          geojsonLoadedForStateRef.current = selectedStateId;
        } catch (error) {
          if (cancelled) return;
          // Geojson failed: clear and bail. We do not preserve any
          // previous choropleth because the requested state has no
          // geometry on the source.
          districtGeojsonRef.current = emptyFeatureCollection;
          geojsonLoadedForStateRef.current = null;
          source.setData(emptyFeatureCollection as any);
          setHasChoropleth(false);
          setMapLoading(false);
          return;
        }
      }

      const start = monthStringToYearMonth(startMonth);
      const end = monthStringToYearMonth(endMonth);
      if (!start || !end) {
        if (!cancelled) {
          // We have valid geometry but no valid range. Surface the
          // uncoloured geometry so the user can still see districts.
          if (stateChanged) source.setData(baseGeojson as any);
          setMapLoading(false);
        }
        return;
      }

      try {
        const stats = await fetchStateDistrictStatistics(
          selectedStateId,
          selectedVariable,
          start.year,
          start.month,
          end.year,
          end.month,
        );
        if (cancelled) return;
        applyChoropleth(source, baseGeojson as any, stats);
        if (!cancelled) {
          setHasChoropleth(true);
          setMapLoading(false);
        }
      } catch (error) {
        if (cancelled) return;
        // Stats failed. If the state changed we must surface the new
        // geometry even without colour so the user knows the request
        // reached the new state. If the state is unchanged we keep the
        // previous choropleth + legend intact because they are still
        // meaningful for the current geometry; the next successful
        // refetch will replace them atomically.
        if (stateChanged) {
          source.setData(baseGeojson as any);
        }
        setMapLoading(false);
      }
    }

    if (map.isStyleLoaded()) {
      run(map);
    } else {
      map.once("load", () => run(map));
    }

    return () => {
      cancelled = true;
    };
  }, [emptyFeatureCollection, selectedStateId, selectedVariable, startMonth, endMonth]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;

    const districtId = selectedDistrictId ?? "";
    if (map.getLayer("districts-selected-fill")) {
      map.setFilter("districts-selected-fill", ["==", ["get", "district_id"], districtId]);
    }
    if (map.getLayer("districts-selected-line")) {
      map.setFilter("districts-selected-line", ["==", ["get", "district_id"], districtId]);
    }

    if (!selectedDistrictId) return;
    const geojson = districtGeojsonRef.current;
    const feature = geojson?.features?.find(
      (f: any) => f?.properties?.district_id === selectedDistrictId
    );
    const geometry = feature?.geometry;
    if (!geometry) return;

    const bounds = (() => {
      const coords: any[] = [];
      const pushCoords = (c: any) => {
        if (!c) return;
        if (typeof c[0] === "number" && typeof c[1] === "number") {
          coords.push(c);
          return;
        }
        for (const child of c) pushCoords(child);
      };
      pushCoords(geometry.coordinates);
      if (coords.length === 0) return null;
      let minX = coords[0][0], minY = coords[0][1], maxX = coords[0][0], maxY = coords[0][1];
      for (const [x, y] of coords) {
        if (x < minX) minX = x;
        if (y < minY) minY = y;
        if (x > maxX) maxX = x;
        if (y > maxY) maxY = y;
      }
      return [[minX, minY], [maxX, maxY]] as [[number, number], [number, number]];
    })();
    if (!bounds) return;

    map.fitBounds(bounds, {
      padding: { top: 80, bottom: 80, left: 80, right: 80 },
      duration: 700,
    });
  }, [selectedDistrictId]);

  const formatValue = (v: number) => {
    if (v === 0) return "0";
    return v.toFixed(v % 1 === 0 ? 0 : 1);
  };

  return (
    <div className="absolute inset-0 z-0 overflow-hidden">
      <div ref={mapContainerRef} className="h-full w-full" />
      
      {legendThresholds && (
        <div className="absolute bottom-4 right-4 z-10 pointer-events-none">
          <div className="rounded-md border border-slate-200 bg-white px-3 py-2.5 min-w-[240px]">
            <div className="text-[11px] font-semibold uppercase tracking-wider text-slate-500 mb-2">
              Distribution Scale ({selectedVariable})
            </div>
            <div className="relative mb-1.5">
              <div
                className="h-3 w-full rounded-sm border border-slate-200"
                style={{
                  background:
                    "linear-gradient(90deg, #F7FBFF 0%, #C6DBEF 25%, #6BAED6 50%, #2171B5 75%, #08306B 100%)",
                }}
              />
            </div>
            <div className="flex justify-between text-[10px] font-medium text-slate-600">
              <div className="flex flex-col items-start">
                <span>p5</span>
                <span className="font-semibold text-slate-900 tabular-nums">{formatValue(legendThresholds.p5)}</span>
              </div>
              <div className="flex flex-col items-center">
                <span>p25</span>
                <span className="font-semibold text-slate-900 tabular-nums">{formatValue(legendThresholds.p25)}</span>
              </div>
              <div className="flex flex-col items-center">
                <span>p50</span>
                <span className="font-semibold text-slate-700 tabular-nums">{formatValue(legendThresholds.p50)}</span>
              </div>
              <div className="flex flex-col items-center">
                <span>p75</span>
                <span className="font-semibold text-slate-900 tabular-nums">{formatValue(legendThresholds.p75)}</span>
              </div>
              <div className="flex flex-col items-end">
                <span>p95</span>
                <span className="font-semibold text-slate-900 tabular-nums">{formatValue(legendThresholds.p95)}</span>
              </div>
            </div>
          </div>
        </div>
      )}

      {mapLoading && (
        <div className="absolute bottom-4 left-4 z-10 pointer-events-none">
          <div
            className="flex items-center gap-1.5 rounded-full bg-slate-900/80 px-3 py-1.5 text-[11px] font-medium text-white shadow-sm backdrop-blur-sm"
            role="status"
            aria-live="polite"
          >
            <span
              className="inline-block h-3 w-3 animate-spin rounded-full border border-white/40 border-t-white"
              aria-hidden="true"
            />
            Updating…
          </div>
        </div>
      )}
    </div>
  );
}

export default HydraMap;