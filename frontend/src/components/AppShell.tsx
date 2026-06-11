import { useAppStore } from "../stores/useAppStore";
import HydraHeader from "./header/HydraHeader";
import DataExplorer from "./data-explorer/DataExplorer";
import SelectedLocation from "./selected-location/SelectedLocation";
import HydraMap from "./map/HydraMap";
import BottomPanel from "./bottom-panel/BottomPanel";

export default function AppShell() {
  const leftSidebarOpen = useAppStore((s) => s.leftSidebarOpen);
  const rightSidebarOpen = useAppStore((s) => s.rightSidebarOpen);

  return (
    /* Root viewport frame with dark fallback to mask tile loading */
    <div className="relative h-screen w-screen overflow-hidden bg-slate-950 text-slate-900 subpixel-antialiased select-none">
      
      {/* LAYER 1: BASE MAP CANVAS (Stays edge-to-edge permanently) */}
      <div className="absolute inset-0 z-0">
        <HydraMap />
      </div>

      {/* LAYER 2: INTERACTIVE UI LAYOUT TREE (Passes clicks through to map) */}
      <div className="absolute inset-0 z-10 flex flex-col pointer-events-none p-6 gap-6">
        
        {/* Persistent Floating Header Card Container */}
        <header className="w-full flex-shrink-0 pointer-events-auto">
          <HydraHeader />
        </header>

        {/* Floating Controls Grid Layout */}
        <main className="w-full flex-1 min-h-0 flex justify-between items-start">
          
          {/* Left Floating Controller (Handles its own sizing internally) */}
          <aside className="pointer-events-auto">
            <DataExplorer />
          </aside>

          {/* Right Floating Info Card (Clean hardware-accelerated glide) */}
          <aside 
            className={`w-[360px] flex-shrink-0 transition-all duration-300 pointer-events-auto ${
              rightSidebarOpen 
                ? "opacity-100 translate-x-0" 
                : "opacity-0 translate-x-10 pointer-events-none"
            }`}
          >
            <SelectedLocation />
          </aside>
          
        </main>
      </div>

      {/* LAYER 3: INDEPENDENT SCREEN CENTRIC UI (Bottom Analytics Graph) */}
      <div className="absolute bottom-6 left-1/2 -translate-x-1/2 w-full max-w-3xl px-6 z-20 pointer-events-auto">
        <BottomPanel />
      </div>
      
    </div>
  );
}