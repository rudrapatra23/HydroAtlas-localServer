import { useEffect, useMemo, useRef, useState } from "react";
import maplibregl, {
  Map as MapLibreMap,
  StyleSpecification,
} from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { monthStringToYearMonth, useAppStore } from "../../stores/useAppStore";
import {
  DistrictRasterClipResponse,
  getDistrictRasterClip,
  getDistrictRasterClipRange,
  getDistrictsGeojson,
} from "../../api/boundaries";

import { getDisplayUnit, toDisplayValue } from "../../stores/districtDataStore";

const DISTRICT_SOURCE_ID = "districts";
const DISTRICT_RASTER_SOURCE_ID = "district-raster";
const DISTRICT_FILL_LAYER_ID = "districts-fill";
const DISTRICT_SELECTED_FILL_LAYER_ID = "districts-selected-fill";
const DISTRICT_SELECTED_LINE_LAYER_ID = "districts-selected-line";
const DISTRICT_RASTER_FILL_LAYER_ID = "district-raster-fill";
const DISTRICT_RASTER_LINE_LAYER_ID = "district-raster-line";

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

const VARIABLE_COLOR_STOPS = {
  precipitation: ["#BAE6FD", "#38BDF8", "#0284C7", "#082F49"],
  soil_moisture: ["#BBF7D0", "#4ADE80", "#16A34A", "#064E3B"],
  surface_runoff: ["#FED7AA", "#FB923C", "#EA580C", "#7C2D12"],
} as const;

function formatLegendValue(value: number): string {
  if (!Number.isFinite(value)) return "n/a";
  return value.toFixed(Math.abs(value) >= 100 ? 0 : 1);
}

function formatMonthLabel(result: DistrictRasterClipResponse | undefined): string {
  if (!result) return "";
  return `${result.year}-${String(result.month).padStart(2, "0")}`;
}

function HydraMap() {
  const mapContainerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<MapLibreMap | null>(null);
  const selectedStateId = useAppStore((state) => state.selectedStateId);
  const selectedDistrictId = useAppStore((state) => state.selectedDistrictId);
  const selectedVariable = useAppStore((state) => state.selectedVariable);
  const endMonth = useAppStore((state) => state.endMonth);
  const startMonth = useAppStore((state) => state.startMonth);
  const rasterLayerEnabled = useAppStore((state) => {
    if (state.selectedVariable === "precipitation") return state.layers.rainfall.enabled;
    if (state.selectedVariable === "soil_moisture") return state.layers["soil-moisture"].enabled;
    return state.layers.runoff.enabled;
  });
  const setSelectedStateId = useAppStore((state) => state.setSelectedStateId);
  const setSelectedDistrictId = useAppStore((state) => state.setSelectedDistrictId);
  const districtGeojsonRef = useRef<any | null>(null);
  const districtRasterRef = useRef<DistrictRasterClipResponse | null>(null);
  const geojsonLoadedForStateRef = useRef<string | null>(null);
  const selectedStateIdRef = useRef<string | null>(selectedStateId);

  useEffect(() => {
    selectedStateIdRef.current = selectedStateId;
  }, [selectedStateId]);

  // Holds every month's clipped result when the user picks a multi-month
  // range. Empty for single-month selections (we don't need a scrubber
  // for one month).
  const [rangeResults, setRangeResults] = useState<DistrictRasterClipResponse[]>([]);
  const [activeMonthIndex, setActiveMonthIndex] = useState(0);
  const [, setBoundaryLoading] = useState(false);
  const [, setRasterLoading] = useState(false);
  const [legendState, setLegendState] = useState<{
    label: string;
    min: number;
    p25: number;
    median: number;
    p75: number;
    max: number;
  } | null>(null);

  const emptyFeatureCollection = useMemo(
    () => ({ type: "FeatureCollection", features: [] as any[] }),
    [],
  );

  /**
   * Resolved date range for the raster fetch.
   * Both start and end must parse correctly before any fetch is issued.
   * When start === end the range is a single month and we use the
   * cheaper /raster-clip endpoint; otherwise /raster-clip-range.
   */
  const rasterDateRange = useMemo(() => {
    const s = monthStringToYearMonth(startMonth);
    const e = monthStringToYearMonth(endMonth);
    if (!s || !e) return null;
    return { start: s, end: e, startStr: startMonth, endStr: endMonth };
  }, [startMonth, endMonth]);

  function setRasterData(
    mapInstance: MapLibreMap,
    response: DistrictRasterClipResponse | null,
  ) {
    const source = mapInstance.getSource(DISTRICT_RASTER_SOURCE_ID) as any;
    if (!source) return;
    if (!response) {
      source.setData(emptyFeatureCollection as any);
      return;
    }
    source.setData({
      ...response.feature_collection,
      features: response.feature_collection.features.map((feature) => ({
        ...feature,
        properties: {
          ...feature.properties,
          display_value: toDisplayValue(selectedVariable, feature.properties.value),
        },
      })),
    } as any);
  }

  function setRasterPaint(
    mapInstance: MapLibreMap,
    response: DistrictRasterClipResponse | null,
  ) {
    if (!mapInstance.getLayer(DISTRICT_RASTER_FILL_LAYER_ID)) return;
    if (!response || response.summary.valid_cells === 0) {
      mapInstance.setPaintProperty(DISTRICT_RASTER_FILL_LAYER_ID, "fill-opacity", 0);
      return;
    }

    const colors = VARIABLE_COLOR_STOPS[selectedVariable];
    const min = toDisplayValue(selectedVariable, response.summary.min);
    const p25 = toDisplayValue(selectedVariable, response.summary.p25);
    const p75 = toDisplayValue(selectedVariable, response.summary.p75);
    const max = toDisplayValue(selectedVariable, response.summary.max);
    const fillColor =
      max <= min
        ? ["case", ["has", "display_value"], colors[2], "rgba(0,0,0,0)"]
        : [
            "interpolate",
            ["linear"],
            ["get", "display_value"],
            min, colors[0],
            p25, colors[1],
            p75, colors[2],
            max, colors[3],
          ];
    mapInstance.setPaintProperty(
      DISTRICT_RASTER_FILL_LAYER_ID,
      "fill-color",
      fillColor as any,
    );
    mapInstance.setPaintProperty(
      DISTRICT_RASTER_FILL_LAYER_ID,
      "fill-opacity",
      ["case", ["has", "display_value"], 0.62, 0],
    );
  }

  function setRasterLegend(response: DistrictRasterClipResponse | null) {
    if (!response || response.summary.valid_cells === 0) {
      setLegendState(null);
      return;
    }
    setLegendState({
      label: `${response.variable_long_name} (${getDisplayUnit(selectedVariable)})`,
      min: toDisplayValue(selectedVariable, response.summary.min),
      p25: toDisplayValue(selectedVariable, response.summary.p25),
      median: toDisplayValue(selectedVariable, response.summary.median),
      p75: toDisplayValue(selectedVariable, response.summary.p75),
      max: toDisplayValue(selectedVariable, response.summary.max),
    });
  }

  // Renders one month's result onto the map and updates the legend.
  // Shared by both the initial fetch and the scrubber, so dragging the
  // slider and loading a fresh range behave identically.
  function showResultOnMap(mapInstance: MapLibreMap, result: DistrictRasterClipResponse) {
    districtRasterRef.current = result;
    setRasterData(mapInstance, result);
    setRasterPaint(mapInstance, result);
    setRasterLegend(result);
  }

  function clearRasterFromMap(mapInstance: MapLibreMap) {
    districtRasterRef.current = null;
    setRangeResults([]);
    setActiveMonthIndex(0);
    setRasterData(mapInstance, null);
    setRasterPaint(mapInstance, null);
    setRasterLegend(null);
  }

  // Lets the user scrub through already-fetched months without
  // refetching anything from the backend.
  function handleMonthIndexChange(index: number) {
    const map = mapRef.current;
    const result = rangeResults[index];
    if (!map || !result) return;
    setActiveMonthIndex(index);
    showResultOnMap(map, result);
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
        },
      );

      map.addSource(DISTRICT_SOURCE_ID, {
        type: "geojson",
        data: emptyFeatureCollection as any,
      });
      map.addSource(DISTRICT_RASTER_SOURCE_ID, {
        type: "geojson",
        data: emptyFeatureCollection as any,
      });

      map.addLayer({
        id: DISTRICT_RASTER_FILL_LAYER_ID,
        type: "fill",
        source: DISTRICT_RASTER_SOURCE_ID,
        paint: {
          "fill-color": "rgba(0,0,0,0)",
          "fill-opacity": 0,
        },
      });
      map.addLayer({
        id: DISTRICT_RASTER_LINE_LAYER_ID,
        type: "line",
        source: DISTRICT_RASTER_SOURCE_ID,
        paint: {
          "line-color": "#0F172A",
          "line-width": 0.1,
          "line-opacity": 0.15,
        },
      });

      map.addLayer({
        id: DISTRICT_FILL_LAYER_ID,
        type: "fill",
        source: DISTRICT_SOURCE_ID,
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
      map.addLayer({
        id: "districts-line",
        type: "line",
        source: DISTRICT_SOURCE_ID,
        paint: {
          "line-color": "#1E293B",
          "line-width": 0.5,
          "line-opacity": 0.25,
        },
      });
      map.addLayer({
        id: DISTRICT_SELECTED_FILL_LAYER_ID,
        type: "fill",
        source: DISTRICT_SOURCE_ID,
        filter: ["==", ["get", "district_id"], ""],
        paint: {
          "fill-color": "#FFFFFF",
          "fill-opacity": 0.1,
        },
      });
      map.addLayer({
        id: DISTRICT_SELECTED_LINE_LAYER_ID,
        type: "line",
        source: DISTRICT_SOURCE_ID,
        filter: ["==", ["get", "district_id"], ""],
        paint: {
          "line-color": "#0F172A",
          "line-width": 1.5,
          "line-opacity": 0.9,
        },
      });
    });

    map.on("click", (event) => {
      const features = map.queryRenderedFeatures(event.point, {
        layers: [DISTRICT_FILL_LAYER_ID],
      });
      const feature = features[0];
      const districtId = feature?.properties?.district_id as string | undefined;
      const stateId = feature?.properties?.state_id as string | undefined;
      if (districtId && stateId) {
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
  }, [emptyFeatureCollection, setSelectedDistrictId, setSelectedStateId]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;

    let cancelled = false;
    setBoundaryLoading(true);
    const loadingToken = useAppStore.getState().beginLoading(
      "Fetching district boundaries",
      "map",
    );

    async function run(mapInstance: MapLibreMap) {
      const source = mapInstance.getSource(DISTRICT_SOURCE_ID) as any;
      if (!source) {
        if (!cancelled) {
          setBoundaryLoading(false);
          useAppStore.getState().endLoading(loadingToken);
        }
        return;
      }

      if (!selectedStateId) {
        if (cancelled) return;
        districtGeojsonRef.current = emptyFeatureCollection;
        geojsonLoadedForStateRef.current = null;
        source.setData(emptyFeatureCollection as any);
        setBoundaryLoading(false);
        useAppStore.getState().endLoading(loadingToken);
        return;
      }

      const stateChanged = geojsonLoadedForStateRef.current !== selectedStateId;
      if (stateChanged || !districtGeojsonRef.current) {
        try {
          const geojson = await getDistrictsGeojson(selectedStateId);
          if (cancelled) return;
          districtGeojsonRef.current = geojson as any;
          geojsonLoadedForStateRef.current = selectedStateId;
          source.setData(geojson as any);
        } catch (error) {
          if (cancelled) return;
          districtGeojsonRef.current = emptyFeatureCollection;
          geojsonLoadedForStateRef.current = null;
          source.setData(emptyFeatureCollection as any);
          setBoundaryLoading(false);
          useAppStore.getState().endLoading(loadingToken);
          return;
        }
      }

      setBoundaryLoading(false);
      useAppStore.getState().endLoading(loadingToken);
    }

    if (map.isStyleLoaded()) {
      run(map);
    } else {
      map.once('load', () => run(map));
    }

    return () => {
      cancelled = true;
      useAppStore.getState().endLoading(loadingToken);
    };
  }, [emptyFeatureCollection, selectedStateId]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;

    let cancelled = false;
    // Reset the scrubber immediately so the slider always reflects the
    // pending fetch, even when the new range has the same length as the
    // old one (which would make setActiveMonthIndex(lastIndex) a no-op
    // if React bails out of the update because the value didn't change).
    setRangeResults([]);
    setActiveMonthIndex(0);
    setRasterLoading(true);
    const loadingToken = useAppStore.getState().beginLoading(
      "Fetching ERA5-Land raster from S3",
      "map",
    );

    async function run(mapInstance: MapLibreMap) {
      if (!mapInstance.getSource(DISTRICT_RASTER_SOURCE_ID)) {
        if (!cancelled) {
          setRasterLoading(false);
          useAppStore.getState().endLoading(loadingToken);
        }
        return;
      }

      if (!selectedDistrictId || !rasterDateRange || !rasterLayerEnabled) {
        if (!cancelled) clearRasterFromMap(mapInstance);
        if (!cancelled) {
          setRasterLoading(false);
          useAppStore.getState().endLoading(loadingToken);
        }
        return;
      }

      try {
        const { start, end, startStr, endStr } = rasterDateRange;
        const isSingleMonth =
          start.year === end.year && start.month === end.month;

        if (isSingleMonth) {
          useAppStore.getState().updateLoading(
            loadingToken,
            "Clipping raster to district boundary",
          );
          const response = await getDistrictRasterClip(selectedDistrictId, {
            year: end.year,
            month: end.month,
            variable: selectedVariable,
          });
          if (cancelled) return;
          useAppStore.getState().updateLoading(loadingToken, "Rendering raster layer");
          setRangeResults([]);
          setActiveMonthIndex(0);
          showResultOnMap(mapInstance, response);
        } else {
          useAppStore.getState().updateLoading(
            loadingToken,
            "Clipping raster across date range",
          );
          const response = await getDistrictRasterClipRange(selectedDistrictId, {
            start: startStr,
            end: endStr,
            variable: selectedVariable,
          });
          if (cancelled) return;

          const results = response.results;
          if (results.length === 0) {
            clearRasterFromMap(mapInstance);
            return;
          }

          useAppStore.getState().updateLoading(loadingToken, "Rendering raster layer");
          setRangeResults(results);
          const lastIndex = results.length - 1;
          setActiveMonthIndex(lastIndex);
          showResultOnMap(mapInstance, results[lastIndex]);
        }
      } catch (error) {
        if (cancelled) return;
        clearRasterFromMap(mapInstance);
      } finally {
        if (!cancelled) {
          setRasterLoading(false);
          useAppStore.getState().endLoading(loadingToken);
        }
      }
    }

    if (map.isStyleLoaded()) {
      run(map);
    } else {
      map.once('load', () => run(map));
    }

    return () => {
      cancelled = true;
      useAppStore.getState().endLoading(loadingToken);
    };
    // Re-run whenever the district, variable, layer toggle, or either
    // date-range bound changes so the raster stays in sync.
  }, [rasterDateRange, rasterLayerEnabled, selectedDistrictId, selectedVariable]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;

    const districtId = selectedDistrictId ?? "";
    if (map.getLayer(DISTRICT_SELECTED_FILL_LAYER_ID)) {
      map.setFilter(DISTRICT_SELECTED_FILL_LAYER_ID, ["==", ["get", "district_id"], districtId]);
    }
    if (map.getLayer(DISTRICT_SELECTED_LINE_LAYER_ID)) {
      map.setFilter(DISTRICT_SELECTED_LINE_LAYER_ID, ["==", ["get", "district_id"], districtId]);
    }

    if (!selectedDistrictId) return;
    const geojson = districtGeojsonRef.current;
    const feature = geojson?.features?.find(
      (f: any) => f?.properties?.district_id === selectedDistrictId,
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
      return [[minX, minY], [maxX, maxY]] as [[number, number], [number, number]];
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

      {rangeResults.length > 1 && selectedDistrictId && rasterLayerEnabled && (
        <div className="absolute top-4 left-1/2 z-10 -translate-x-1/2">
          <div className="flex items-center gap-3 rounded-md border border-slate-200 bg-white/95 px-4 py-2.5 shadow-sm backdrop-blur-sm">
            <span className="whitespace-nowrap text-[11px] font-medium text-slate-700">
              {formatMonthLabel(rangeResults[activeMonthIndex])}
            </span>
            <input
              type="range"
              min={0}
              max={rangeResults.length - 1}
              step={1}
              value={activeMonthIndex}
              onChange={(e) => handleMonthIndexChange(Number(e.target.value))}
              className="w-40 white-thumb-slider"
              style={{
                background: `linear-gradient(to right, #0ea5e9 ${(activeMonthIndex / Math.max(1, rangeResults.length - 1)) * 100}%, #e2e8f0 ${(activeMonthIndex / Math.max(1, rangeResults.length - 1)) * 100}%)`
              }}
              aria-label="Select month within range"
            />
          </div>
        </div>
      )}

      {legendState && selectedDistrictId && rasterLayerEnabled && (
        <div className="absolute bottom-4 right-4 z-10 pointer-events-none">
          <div className="min-w-[240px] rounded-md border border-slate-200 bg-white/95 px-3 py-2.5 shadow-sm backdrop-blur-sm">
            <div className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-slate-500">
              {legendState.label}
            </div>
            <div
              className="mb-1.5 h-3 w-full rounded-sm border border-slate-200"
              style={{
                background: `linear-gradient(90deg, ${VARIABLE_COLOR_STOPS[selectedVariable][0]} 0%, ${VARIABLE_COLOR_STOPS[selectedVariable][1]} 33%, ${VARIABLE_COLOR_STOPS[selectedVariable][2]} 66%, ${VARIABLE_COLOR_STOPS[selectedVariable][3]} 100%)`,
              }}
            />
            <div className="flex justify-between text-[10px] font-medium text-slate-600">
              <span>{formatLegendValue(legendState.min)}</span>
              <span>{formatLegendValue(legendState.p25)}</span>
              <span>{formatLegendValue(legendState.median)}</span>
              <span>{formatLegendValue(legendState.p75)}</span>
              <span>{formatLegendValue(legendState.max)}</span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default HydraMap;
