import { useEffect, useMemo, useRef, useState } from "react";
import maplibregl, {
  Map as MapLibreMap,
  StyleSpecification,
} from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { useAppStore } from "../../stores/useAppStore";
import { getDistrictsGeojson } from "../../api/boundaries";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";
const DEFAULT_STATS_YEAR = 2024;
const DEFAULT_STATS_MONTH = 1;

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
  const setSelectedStateId = useAppStore((state) => state.setSelectedStateId);
  const setSelectedDistrictId = useAppStore((state) => state.setSelectedDistrictId);
  const districtGeojsonRef = useRef<any | null>(null);
  const geojsonLoadedForStateRef = useRef<string | null>(null);

  const [legendThresholds, setLegendThresholds] = useState<LegendThresholds | null>(null);

  const emptyFeatureCollection = useMemo(
    () => ({ type: "FeatureCollection", features: [] as any[] }),
    []
  );

  async function fetchStateDistrictStatistics(stateId: string, variable: string) {
    const response = await fetch(
      `${API_BASE_URL}/states/${encodeURIComponent(stateId)}/districts/statistics`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          year: DEFAULT_STATS_YEAR,
          month: DEFAULT_STATS_MONTH,
          variable,
        }),
      }
    );
    if (!response.ok) {
      throw new Error(`Request failed: ${response.status} ${response.statusText}`);
    }
    return (await response.json()) as {
      state_id: string;
      year: number;
      month: number;
      variable: string;
      districts: Array<{ district_id: string; mean: number; min: number; max: number }>;
    };
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
        if (selectedStateId !== stateId) {
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
  }, [emptyFeatureCollection, selectedStateId, setSelectedDistrictId, setSelectedStateId]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;

    let cancelled = false;

    async function loadDistricts(mapInstance: MapLibreMap) {
      const source = mapInstance.getSource("districts") as any;
      if (!source) return;

      if (!selectedStateId) {
        districtGeojsonRef.current = emptyFeatureCollection;
        geojsonLoadedForStateRef.current = null;
        setLegendThresholds(null);
        source.setData(emptyFeatureCollection as any);
        return;
      }

      try {
        const geojson = await getDistrictsGeojson(selectedStateId);
        if (cancelled) return;
        districtGeojsonRef.current = geojson as any;
        geojsonLoadedForStateRef.current = selectedStateId;

        try {
          const stats = await fetchStateDistrictStatistics(selectedStateId, selectedVariable);
          if (cancelled) return;
          applyChoropleth(source, geojson as any, stats);
        } catch (error) {
          if (cancelled) return;
          source.setData(geojson as any);
        }
      } catch (error) {
        if (cancelled) return;
        districtGeojsonRef.current = emptyFeatureCollection;
        geojsonLoadedForStateRef.current = null;
        source.setData(emptyFeatureCollection as any);
      }
    }

    if (map.isStyleLoaded()) {
      loadDistricts(map);
    } else {
      map.once("load", () => loadDistricts(map));
    }

    return () => {
      cancelled = true;
    };
  }, [emptyFeatureCollection, selectedStateId, selectedVariable]);

  useEffect(() => {
    const map = mapRef.current;
    const source = map?.getSource("districts") as any;
    const baseGeojson = districtGeojsonRef.current;
    if (!map || !source || !baseGeojson || !selectedStateId) return;
    if (geojsonLoadedForStateRef.current !== selectedStateId) return;

    let cancelled = false;

    async function refetchAndApply() {
      try {
        const stats = await fetchStateDistrictStatistics(selectedStateId, selectedVariable);
        if (cancelled) return;
        applyChoropleth(source, baseGeojson, stats);
      } catch (error) {
        if (cancelled) return;
        source.setData(baseGeojson);
      }
    }

    refetchAndApply();

    return () => {
      cancelled = true;
    };
  }, [selectedStateId, selectedVariable]);

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
          <div className="rounded-[14px] border border-slate-200 bg-white/95 px-4 py-3 shadow-[0_12px_40px_rgba(15,23,42,0.08)] backdrop-blur-[22px] min-w-[240px]">
            <div className="text-[11px] font-bold tracking-wide uppercase text-slate-500 mb-2">
              Distribution Scale ({selectedVariable})
            </div>
            <div className="relative mb-1.5">
              {/* Updated: Matching CSS gradient for legend element */}
              <div
                className="h-3 w-full rounded-sm border border-slate-200/60"
                style={{
                  background:
                    "linear-gradient(90deg, #F7FBFF 0%, #C6DBEF 25%, #6BAED6 50%, #2171B5 75%, #08306B 100%)",
                }}
              />
            </div>
            <div className="flex justify-between text-[10px] font-medium text-slate-600">
              <div className="flex flex-col items-start">
                <span>p5</span>
                <span className="font-semibold text-slate-900">{formatValue(legendThresholds.p5)}</span>
              </div>
              <div className="flex flex-col items-center">
                <span>p25</span>
                <span className="font-semibold text-slate-900">{formatValue(legendThresholds.p25)}</span>
              </div>
              <div className="flex flex-col items-center">
                <span>p50</span>
                <span className="font-semibold text-slate-700">{formatValue(legendThresholds.p50)}</span>
              </div>
              <div className="flex flex-col items-center">
                <span>p75</span>
                <span className="font-semibold text-slate-900">{formatValue(legendThresholds.p75)}</span>
              </div>
              <div className="flex flex-col items-end">
                <span>p95</span>
                <span className="font-semibold text-slate-900">{formatValue(legendThresholds.p95)}</span>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default HydraMap;