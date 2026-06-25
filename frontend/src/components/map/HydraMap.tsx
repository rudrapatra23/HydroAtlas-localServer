import { useEffect, useMemo, useRef } from "react";
import maplibregl, {
  Map,
  StyleSpecification,
} from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { useAppStore } from "../../stores/useAppStore";
import {
  getDistrictsGeojson,
} from "../../api/boundaries";

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

function HydraMap() {
  const mapContainerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<Map | null>(null);
  const selectedStateId = useAppStore((state) => state.selectedStateId);
  const selectedDistrictId = useAppStore((state) => state.selectedDistrictId);
  const selectedVariable = useAppStore((state) => state.selectedVariable);
  const setSelectedStateId = useAppStore((state) => state.setSelectedStateId);
  const setSelectedDistrictId = useAppStore((state) => state.setSelectedDistrictId);
  const districtGeojsonRef = useRef<any | null>(null);
  const geojsonLoadedForStateRef = useRef<string | null>(null);

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
    const json = (await response.json()) as {
      state_id: string;
      year: number;
      month: number;
      variable: string;
      districts: Array<{ district_id: string; mean: number; min: number; max: number }>;
    };
    console.log("[choropleth] stats response", json);
    return json;
  }

  function applyChoropleth(
    source: any,
    baseGeojson: any,
    stats: {
      districts: Array<{ district_id: string; mean: number }>;
    }
  ) {
    const byDistrictId = new Map<string, number>();
    for (const item of stats.districts) {
      if (typeof item.mean === "number" && !Number.isNaN(item.mean)) {
        byDistrictId.set(item.district_id, item.mean);
      }
    }

    const features = (baseGeojson.features ?? []) as any[];
    let featureWithIdCount = 0;
    let matchedCount = 0;
    const missingIds: string[] = [];
    for (const f of features) {
      const districtId = f?.properties?.district_id as string | undefined;
      if (!districtId) continue;
      featureWithIdCount += 1;
      if (byDistrictId.has(districtId)) {
        matchedCount += 1;
      } else if (missingIds.length < 20) {
        missingIds.push(districtId);
      }
    }
    console.log("[choropleth] geojson features", {
      totalFeatures: features.length,
      featuresWithDistrictId: featureWithIdCount,
      matched: matchedCount,
      allMatched: featureWithIdCount > 0 && matchedCount === featureWithIdCount,
      sampleMissingDistrictIds: missingIds,
    });

    const means = Array.from(byDistrictId.values());
    if (means.length === 0) {
      source.setData(baseGeojson);
      return;
    }

    let min = means[0];
    let max = means[0];
    for (const v of means) {
      if (v < min) min = v;
      if (v > max) max = v;
    }
    const denom = max - min;
    console.log("[choropleth] frontend mean range", { min, max, denom });

    const firstFiveNormalized: Array<{ district_id: string; mean: number; norm: number }> = [];

    const joined = {
      ...baseGeojson,
      features: (baseGeojson.features ?? []).map((f: any) => {
        const districtId = f?.properties?.district_id as string | undefined;
        if (!districtId) return f;
        const mean = byDistrictId.get(districtId);
        if (mean === undefined) {
          return {
            ...f,
            properties: {
              ...f.properties,
              mean: null,
              norm: null,
            },
          };
        }
        const norm = denom === 0 ? 0.5 : (mean - min) / denom;
        if (firstFiveNormalized.length < 5) {
          firstFiveNormalized.push({ district_id: districtId, mean, norm });
        }
        return {
          ...f,
          properties: {
            ...f.properties,
            mean,
            norm,
          },
        };
      }),
    };

    console.log("[choropleth] first five normalized", firstFiveNormalized);
    source.setData(joined);
  }

  useEffect(() => {
    if (!mapContainerRef.current || mapRef.current) {
      return;
    }

    const map = new maplibregl.Map({
      container: mapContainerRef.current,
      style: lightBasemapStyle,
      // Remove fixed bounds so user can pan outside India after load
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
      // Fit map to India bounds with padding on load
      map.fitBounds(
        [
          [67.5, 6], // SW
          [97.5, 37.5], // NE
        ],
        {
          padding: { top: 50, bottom: 50, left: 50, right: 50 },
          duration: 0,
        }
      );

      if (!map.getSource("districts")) {
        map.addSource("districts", {
          type: "geojson",
          data: emptyFeatureCollection as any,
        });
      }

      if (!map.getLayer("districts-fill")) {
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
                0,
                "#2563EB",
                0.25,
                "#16A34A",
                0.5,
                "#FACC15",
                0.75,
                "#F97316",
                1,
                "#DC2626",
              ],
              "#2563EB",
            ],
            "fill-opacity": [
              "case",
              ["has", "norm"],
              0.55,
              0.08,
            ],
          },
        });
      }

      if (!map.getLayer("districts-line")) {
        map.addLayer({
          id: "districts-line",
          type: "line",
          source: "districts",
          paint: {
            "line-color": "#1D4ED8",
            "line-width": 1,
            "line-opacity": 0.5,
          },
        });
      }

      if (!map.getLayer("districts-selected-fill")) {
        map.addLayer({
          id: "districts-selected-fill",
          type: "fill",
          source: "districts",
          filter: ["==", ["get", "district_id"], ""],
          paint: {
            "fill-color": "#F59E0B",
            "fill-opacity": 0.28,
          },
        });
      }

      if (!map.getLayer("districts-selected-line")) {
        map.addLayer({
          id: "districts-selected-line",
          type: "line",
          source: "districts",
          filter: ["==", ["get", "district_id"], ""],
          paint: {
            "line-color": "#F59E0B",
            "line-width": 2.5,
            "line-opacity": 0.95,
          },
        });
      }
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
  }, [
    emptyFeatureCollection,
    selectedStateId,
    setSelectedDistrictId,
    setSelectedStateId,
  ]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;

    let cancelled = false;

    async function loadDistricts(mapInstance: Map) {
      const source = mapInstance.getSource("districts") as any;
      if (!source) return;

      if (!selectedStateId) {
        districtGeojsonRef.current = emptyFeatureCollection;
        geojsonLoadedForStateRef.current = null;
        source.setData(emptyFeatureCollection as any);
        return;
      }

      try {
        const geojson = await getDistrictsGeojson(selectedStateId);
        if (cancelled) return;
        districtGeojsonRef.current = geojson as any;
        geojsonLoadedForStateRef.current = selectedStateId;
        source.setData(geojson as any);

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
    if (!map || !source || !baseGeojson) return;
    if (!selectedStateId) return;
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
      map.setFilter("districts-selected-fill", [
        "==",
        ["get", "district_id"],
        districtId,
      ]);
    }
    if (map.getLayer("districts-selected-line")) {
      map.setFilter("districts-selected-line", [
        "==",
        ["get", "district_id"],
        districtId,
      ]);
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
      let minX = coords[0][0];
      let minY = coords[0][1];
      let maxX = coords[0][0];
      let maxY = coords[0][1];
      for (const [x, y] of coords) {
        if (x < minX) minX = x;
        if (y < minY) minY = y;
        if (x > maxX) maxX = x;
        if (y > maxY) maxY = y;
      }
      return [
        [minX, minY],
        [maxX, maxY],
      ] as [[number, number], [number, number]];
    })();
    if (!bounds) return;

    map.fitBounds(bounds, {
      padding: { top: 80, bottom: 80, left: 80, right: 80 },
      duration: 700,
    });
  }, [selectedDistrictId]);

  return (
    <div className="absolute inset-0 z-0 overflow-hidden">
      <div ref={mapContainerRef} className="h-full w-full" />
      <div className="absolute bottom-4 right-4 z-10 pointer-events-none">
        <div className="rounded-[14px] border border-slate-900/6 bg-white/92 px-3 py-2.5 shadow-[0_12px_40px_rgba(15,23,42,0.08)] backdrop-blur-[22px]">
          <div className="text-[11px] font-semibold text-slate-600 mb-1">
            Low → High
          </div>
          <div className="flex items-center gap-2">
            <div
              className="h-2.5 w-28 rounded"
              style={{
                background:
                  "linear-gradient(90deg, #2563EB 0%, #16A34A 25%, #FACC15 50%, #F97316 75%, #DC2626 100%)",
              }}
            />
          </div>
        </div>
      </div>
    </div>
  );
}

export default HydraMap;
