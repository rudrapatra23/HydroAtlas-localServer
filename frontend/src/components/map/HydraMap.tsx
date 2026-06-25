import { useEffect, useMemo, useRef } from "react";
import maplibregl, {
  Map,
  Marker,
  StyleSpecification,
  LngLatLike,
} from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { useAppStore } from "../../stores/useAppStore";
import { getDistrictsGeojson } from "../../api/boundaries";



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

function createMarkerElement() {
  const markerElement = document.createElement("div");

  markerElement.style.width = "18px";
  markerElement.style.height = "18px";
  markerElement.style.borderRadius = "9999px";
  markerElement.style.background =
    "radial-gradient(circle at 30% 30%, #ecfeff 0%, #67e8f9 35%, #06b6d4 100%)";
  markerElement.style.border = "2px solid rgba(255, 255, 255, 0.95)";
  markerElement.style.boxShadow =
    "0 0 0 6px rgba(34, 211, 238, 0.18), 0 12px 28px rgba(8, 145, 178, 0.35)";
  
  // Add drop animation
  markerElement.style.animation = "markerDrop 0.4s cubic-bezier(0.34, 1.56, 0.64, 1)";

  // Add the animation style to the document if not already there
  if (!document.getElementById("marker-drop-animation")) {
    const style = document.createElement("style");
    style.id = "marker-drop-animation";
    style.textContent = `
      @keyframes markerDrop {
        0% {
          transform: translateY(-40px) scale(0.6);
          opacity: 0;
        }
        100% {
          transform: translateY(0) scale(1);
          opacity: 1;
        }
      }
    `;
    document.head.appendChild(style);
  }

  return markerElement;
}

function HydraMap() {
  const mapContainerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<Map | null>(null);
  const markerRef = useRef<Marker | null>(null);
  const selectedPoint = useAppStore((state) => state.selectedPoint);
  const setSelectedPoint = useAppStore((state) => state.setSelectedPoint);
  const selectedStateId = useAppStore((state) => state.selectedStateId);
  const selectedDistrictId = useAppStore((state) => state.selectedDistrictId);
  const setSelectedStateId = useAppStore((state) => state.setSelectedStateId);
  const setSelectedDistrictId = useAppStore((state) => state.setSelectedDistrictId);
  const districtGeojsonRef = useRef<any | null>(null);

  const emptyFeatureCollection = useMemo(
    () => ({ type: "FeatureCollection", features: [] as any[] }),
    []
  );

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
            "fill-color": "#2563EB",
            "fill-opacity": 0.08,
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
        markerRef.current?.remove();
        setSelectedPoint(null);
        if (selectedStateId !== stateId) {
          setSelectedStateId(stateId);
          queueMicrotask(() => setSelectedDistrictId(districtId));
        } else {
          setSelectedDistrictId(districtId);
        }
        return;
      }

      markerRef.current?.remove();

      markerRef.current = new maplibregl.Marker({
        element: createMarkerElement(),
        anchor: "center",
      })
        .setLngLat(event.lngLat)
        .addTo(map);

      setSelectedPoint({ lat: event.lngLat.lat, lng: event.lngLat.lng });

      map.flyTo({
        center: event.lngLat,
        duration: 900,
        essential: true,
      });
    });

    return () => {
      markerRef.current?.remove();
      map.remove();
      markerRef.current = null;
      mapRef.current = null;
    };
  }, [
    emptyFeatureCollection,
    selectedStateId,
    setSelectedDistrictId,
    setSelectedPoint,
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
        source.setData(emptyFeatureCollection as any);
        return;
      }

      try {
        const geojson = await getDistrictsGeojson(selectedStateId);
        if (cancelled) return;
        districtGeojsonRef.current = geojson as any;
        source.setData(geojson as any);
      } catch (error) {
        if (cancelled) return;
        districtGeojsonRef.current = emptyFeatureCollection;
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
  }, [emptyFeatureCollection, selectedStateId]);

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

  // Update marker if selectedPoint changes externally
  useEffect(() => {
    if (!mapRef.current) return;

    if (selectedPoint) {
      const lngLat: LngLatLike = [selectedPoint.lng, selectedPoint.lat];
      markerRef.current?.remove();
      markerRef.current = new maplibregl.Marker({
        element: createMarkerElement(),
        anchor: "center",
      })
        .setLngLat(lngLat)
        .addTo(mapRef.current);
    } else {
      markerRef.current?.remove();
      markerRef.current = null;
    }
  }, [selectedPoint]);

  // return (
  //   <div className="absolute inset-0 z-0 overflow-hidden">
  //     <div ref={mapContainerRef} className="h-full w-full" />
  //   </div>
  // );
  return (
  <div className="absolute inset-0 z-0 overflow-hidden">
    <div
      ref={mapContainerRef}
      className="h-full w-full"
    />
  </div>
);
}

export default HydraMap;
