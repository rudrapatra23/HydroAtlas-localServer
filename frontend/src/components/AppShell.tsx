
import DataExplorer from "./data-explorer/DataExplorer";
import SelectedLocation from "./selected-location/SelectedLocation";
import HydraMap from "./map/HydraMap";
import BottomPanel from "./bottom-panel/BottomPanel";
import LoadingOverlay from "./LoadingOverlay";

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


  return (
    /* Root viewport frame — the map fills it edge-to-edge. */
    <div className="relative h-screen w-screen overflow-hidden bg-slate-50 text-slate-900 subpixel-antialiased select-none">
      {/* LAYER 1: BASE MAP CANVAS (edge-to-edge) */}
      <div className="absolute inset-0 z-0">
        <HydraMap />
      </div>

      {/* LAYER 2: TOP CONTROLS */}
      <div className="absolute top-4 right-4 z-20 flex pointer-events-none">
        {/* Right floating info card */}
        <div className="pointer-events-auto">
          <SelectedLocation />
        </div>
      </div>

      {/* LAYER 3: LEFT SIDEBAR (Data Explorer) */}
      <div className="absolute top-0 bottom-0 left-0 z-20 pointer-events-auto">
  <DataExplorer />
</div>

      {/* LAYER 4: BOTTOM ANALYSIS PANEL */}
      <div className="absolute bottom-4 left-0 right-0 mx-auto w-full max-w-3xl px-4 z-20 pointer-events-auto">
        <BottomPanel />
      </div>

      {/* LAYER 5: GLOBAL LOADING OVERLAY (above everything) */}
      <LoadingOverlay />
    </div>
  );
}
