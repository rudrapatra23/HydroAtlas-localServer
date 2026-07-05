import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAppStore } from "../stores/useAppStore";
import DataExplorer from "./data-explorer/DataExplorer";
import SelectedLocation from "./selected-location/SelectedLocation";
import HydraMap from "./map/HydraMap";
import BottomPanel from "./bottom-panel/BottomPanel";

/**
 * Studio application shell.
 *
 * Layout: three absolute-positioned layers over a single full-viewport
 * map canvas. The top-left carries a compact Back control; the top-right
 * hosts the Data Explorer (left sidebar). The Selected Region panel
 * sits to the right of the map; the analysis panel anchors the bottom.
 *
 * No studio header bar, no large paddings — the map owns the viewport.
 */
export default function AppShell() {
  const navigate = useNavigate();
  const rightSidebarOpen = useAppStore((s) => s.rightSidebarOpen);
  const [canGoBack, setCanGoBack] = useState(false);

  // ``useNavigate(-1)`` is only meaningful when there is a previous
  // entry in the router stack. On a direct load of ``/studio`` the stack
  // is empty, so we fall back to the landing route ``/``.
  useEffect(() => {
    setCanGoBack(window.history.length > 1);
  }, []);

  const handleBack = () => {
    if (canGoBack) {
      navigate(-1);
    } else {
      navigate("/");
    }
  };

  return (
    /* Root viewport frame — the map fills it edge-to-edge. */
    <div className="relative h-screen w-screen overflow-hidden bg-slate-50 text-slate-900 subpixel-antialiased select-none">

      {/* LAYER 1: BASE MAP CANVAS (edge-to-edge) */}
      <div className="absolute inset-0 z-0">
        <HydraMap />
      </div>

      {/* LAYER 2: TOP CONTROLS (compact, no large header bar) */}
      <div className="absolute top-4 left-4 right-4 z-20 flex items-start justify-between gap-4 pointer-events-none">
        {/* Back control */}
        <div className="pointer-events-auto ">
          <button
            type="button"
            onClick={handleBack}
            className="flex h-9 items-center gap-2 rounded-md border border-slate-200 bg-white px-3 text-sm font-medium text-slate-700 transition-colors hover:bg-slate-50"
            aria-label="Back"
          >
            <span className="material-symbols-rounded text-slate-600" style={{ fontSize: 18 }}>
              arrow_back
            </span>
            Back
          </button>
        </div>

        {/* Right floating info card */}
        <div
          className={`pointer-events-auto transition-all duration-200 ${
            rightSidebarOpen
              ? "opacity-100 translate-x-0"
              : "opacity-0 translate-x-4 pointer-events-none"
          }`}
        >
          <SelectedLocation />
        </div>
      </div>

      {/* LAYER 3: LEFT SIDEBAR (Data Explorer) */}
      <div className="absolute top-16 left-4 z-20 pointer-events-auto">
        <DataExplorer />
      </div>

      {/* LAYER 4: BOTTOM ANALYSIS PANEL */}
      <div className="absolute bottom-4 left-0 right-0 mx-auto w-full max-w-3xl px-4 z-20 pointer-events-auto">
        <BottomPanel />
      </div>

    </div>
  );
}
