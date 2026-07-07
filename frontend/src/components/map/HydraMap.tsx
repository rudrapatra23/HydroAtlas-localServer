import { useEffect, useMemo, useRef, useState } from "react";
import maplibregl, {
  Map as MapLibreMap,
  StyleSpecification,
} from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { useAppStore } from "../../stores/useAppStore";
import { getDistrictsGeojson } from "../../api/boundaries";

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

function HydraMap() {
  const mapContainerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<MapLibreMap | null>(null);
  const selectedStateId = useAppStore((state) => state.selectedStateId);
  const selectedDistrictId = useAppStore((state) => state.selectedDistrictId);
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

  // Boundary-load in-flight indicator. State selection only loads the
  // district polygon overlay; it does NOT trigger any climate raster
  // computation. The "Updating…" badge reflects the GeoJSON fetch, not
  // any choropleth computation.
  const [mapLoading, setMapLoading] = useState(false);

  const emptyFeatureCollection = useMemo(
    () => ({ type: "FeatureCollection", features: [] as any[] }),
    []
  );

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

    // State selection only loads the district polygon overlay.
    // Climate raster statistics for the entire state are NOT computed
    // automatically. The whole-state choropleth endpoint remains
    // available in the backend for an explicit on-demand overview.
    setMapLoading(true);

    async function run(mapInstance: MapLibreMap) {
      const source = mapInstance.getSource("districts") as any;
      if (!source) {
        if (!cancelled) setMapLoading(false);
        return;
      }

      // Deselection: clear geometry, no fetch issued.
      if (!selectedStateId) {
        if (cancelled) return;
        districtGeojsonRef.current = emptyFeatureCollection;
        geojsonLoadedForStateRef.current = null;
        source.setData(emptyFeatureCollection as any);
        setMapLoading(false);
        return;
      }

      // Only re-fetch the GeoJSON when the state actually changes. The
      // same state may be re-selected after a deselect; the cached
      // polygon collection is reused to avoid a redundant network
      // round-trip.
      const stateChanged =
        geojsonLoadedForStateRef.current !== selectedStateId;

      if (stateChanged || !districtGeojsonRef.current) {
        try {
          const geojson = await getDistrictsGeojson(selectedStateId);
          if (cancelled) return;
          districtGeojsonRef.current = geojson as any;
          geojsonLoadedForStateRef.current = selectedStateId;
          source.setData(geojson as any);
        } catch (error) {
          if (cancelled) return;
          // Geojson failed: clear and bail.
          districtGeojsonRef.current = emptyFeatureCollection;
          geojsonLoadedForStateRef.current = null;
          source.setData(emptyFeatureCollection as any);
          setMapLoading(false);
          return;
        }
      }

      setMapLoading(false);
    }

    if (map.isStyleLoaded()) {
      run(map);
    } else {
      map.once("load", () => run(map));
    }

    return () => {
      cancelled = true;
    };
    // State selection only depends on the selected state. Variable,
    // start month and end month do NOT trigger any map refetch because
    // no raster computation happens on the map surface anymore — the
    // map only renders district polygon boundaries.
  }, [emptyFeatureCollection, selectedStateId]);

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

  return (
    <div className="absolute inset-0 z-0 overflow-hidden">
      <div ref={mapContainerRef} className="h-full w-full" />

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