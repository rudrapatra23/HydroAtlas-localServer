import { useAppStore } from "../../stores/useAppStore";
import { motion, AnimatePresence } from "framer-motion";
import { useEffect } from "react";
import { getDistricts, getStates } from "../../api/boundaries";

interface ToggleProps {
  checked: boolean;
  onChange: () => void;
}

function Toggle({ checked, onChange }: ToggleProps) {
  return (
    <button
      type="button"
      onClick={onChange}
      className={`relative h-6 w-11 rounded-full transition-colors duration-200 ease-out ${
        checked ? "bg-blue-600" : "bg-slate-200"
      }`}
    >
      <span
        className={`absolute top-0.5 left-0.5 h-5 w-5 rounded-full bg-white shadow-sm transition-transform duration-200 ease-out ${
          checked ? "translate-x-5" : "translate-x-0"
        }`}
      />
    </button>
  );
}

interface IconContainerProps {
  children: React.ReactNode;
  color?: string;
}

function IconContainer({ children, color }: IconContainerProps) {
  return (
    <div
      className="flex h-9 w-9 items-center justify-center rounded-[12px] transition-all duration-200 ease-out hover:bg-[rgba(15,23,42,0.06)] hover:scale-105"
      style={{ backgroundColor: color || "rgba(15,23,42,0.04)" }}
    >
      {children}
    </div>
  );
}

function DataExplorer() {
  const sidebarOpen = useAppStore((state) => state.leftSidebarOpen);
  const setSidebarOpen = useAppStore((state) => state.setLeftSidebarOpen);
  const layers = useAppStore((state) => state.layers);
  const toggleLayer = useAppStore((state) => state.toggleLayer);
  const states = useAppStore((state) => state.states);
  const districts = useAppStore((state) => state.districts);
  const selectedStateId = useAppStore((state) => state.selectedStateId);
  const selectedDistrictId = useAppStore((state) => state.selectedDistrictId);
  const setStates = useAppStore((state) => state.setStates);
  const setDistricts = useAppStore((state) => state.setDistricts);
  const setSelectedStateId = useAppStore((state) => state.setSelectedStateId);
  const setSelectedDistrictId = useAppStore((state) => state.setSelectedDistrictId);

  // Fetch states on mount
  useEffect(() => {
    async function fetchStates() {
      try {
        const data = await getStates();
        setStates(data.map((item: any) => ({ id: item.state_id, name: item.name })));
      } catch (error) {
        console.error("Failed to fetch states:", error);
      }
    }
    fetchStates();
  }, [setStates]);

  // Fetch districts when selected state changes
  useEffect(() => {
    if (!selectedStateId) {
      setDistricts([]);
      return;
    }
    const stateId = selectedStateId;
    async function fetchDistricts() {
      try {
        const data = await getDistricts(stateId);
        setDistricts(data.map((item: any) => ({ id: item.district_id, name: item.name })));
      } catch (error) {
        console.error("Failed to fetch districts:", error);
      }
    }
    fetchDistricts();
  }, [selectedStateId, setDistricts]);

  return (
    <div className="relative select-none flex items-start">
      <AnimatePresence mode="wait">
        {!sidebarOpen ? (
          /* 1. PERSISTENT FLOATING TRIGGER BUTTON */
          <motion.button
            key="menu-trigger"
            initial={{ opacity: 0, scale: 0.8 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 0.8 }}
            whileHover={{ scale: 1.05 }}
            whileTap={{ scale: 0.95 }}
            transition={{ type: "spring", stiffness: 400, damping: 25 }}
            type="button"
            onClick={() => setSidebarOpen(true)}
            className="flex h-12 w-12 items-center justify-center rounded-[20px] border border-slate-900/6 bg-white/92 shadow-[0_12px_40px_rgba(15,23,42,0.08)] backdrop-blur-[22px] text-slate-600 cursor-pointer"
          >
            <span className="material-symbols-rounded" style={{ fontSize: 24 }}>
              menu
            </span>
          </motion.button>
        ) : (
          /* 2. EXPANDED CONTROL INTERFACE SIDEBAR CARD */
          <motion.div
            key="explorer-card"
            initial={{ opacity: 0, x: -30, scale: 0.98 }}
            animate={{ opacity: 1, x: 0, scale: 1 }}
            exit={{ opacity: 0, x: -30, scale: 0.98 }}
            transition={{ type: "spring", stiffness: 380, damping: 28 }}
            className="w-[320px] rounded-[20px] border border-slate-900/6 bg-white/92 p-5 shadow-[0_12px_40px_rgba(15,23,42,0.08)] backdrop-blur-[22px] flex flex-col"
          >
            {/* Header Controls */}
            <div className="flex items-center justify-between mb-6">
              <p className="text-sm font-semibold text-slate-900 tracking-tight">
                Data Explorer
              </p>
              <motion.button
                whileHover={{ scale: 1.05, backgroundColor: "rgba(15,23,42,0.04)" }}
                whileTap={{ scale: 0.95 }}
                type="button"
                onClick={() => setSidebarOpen(false)}
                className="flex h-8 w-8 items-center justify-center rounded-full transition-colors duration-200 ease-out cursor-pointer"
              >
                <span className="material-symbols-rounded text-slate-500" style={{ fontSize: 20 }}>
                  close
                </span>
              </motion.button>
            </div>

            {/* Content Lists */}
            <div className="space-y-7">
              {/* Region Selection Section */}
              <div className="space-y-3">
                <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-400">
                  Region
                </p>
                <div className="space-y-2.5">
                  {/* State Dropdown */}
                  <div className="rounded-[16px] border border-slate-900/6 bg-slate-50/60 p-3.5">
                    <label className="text-xs text-slate-500 mb-1.5 block">State</label>
                    <select
                      value={selectedStateId || ""}
                      onChange={(e) => setSelectedStateId(e.target.value || null)}
                      className="w-full bg-white border border-slate-200 rounded-lg p-2.5 text-sm text-slate-800 focus:outline-none focus:ring-2 focus:ring-blue-500"
                    >
                      <option value="">Select a state</option>
                      {states.map((state) => (
                        <option key={state.id} value={state.id}>
                          {state.name}
                        </option>
                      ))}
                    </select>
                  </div>
                  {/* District Dropdown */}
                  <div className="rounded-[16px] border border-slate-900/6 bg-slate-50/60 p-3.5">
                    <label className="text-xs text-slate-500 mb-1.5 block">District</label>
                    <select
                      value={selectedDistrictId || ""}
                      onChange={(e) => setSelectedDistrictId(e.target.value || null)}
                      disabled={!selectedStateId}
                      className="w-full bg-white border border-slate-200 rounded-lg p-2.5 text-sm text-slate-800 focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:opacity-50 disabled:cursor-not-allowed"
                    >
                      <option value="">Select a district</option>
                      {districts.map((district) => (
                        <option key={district.id} value={district.id}>
                          {district.name}
                        </option>
                      ))}
                    </select>
                  </div>
                </div>
              </div>

              {/* Dataset Management Section */}
              <div className="space-y-3">
                <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-400">
                  Dataset
                </p>
                <div className="rounded-[16px] border border-slate-900/6 bg-slate-50/60 p-3.5">
                  <div className="flex w-full items-center gap-3">
                    <IconContainer>
                      <span className="material-symbols-rounded text-slate-700" style={{ fontSize: 24 }}>
                        public
                      </span>
                    </IconContainer>
                    <span className="text-sm font-semibold text-slate-700">
                      ERA5-Land
                    </span>
                  </div>
                </div>
              </div>

              {/* GIS Map Layers Layout Configuration */}
              <div className="space-y-3">
                <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-400">
                  Layers
                </p>
                <div className="space-y-2.5">
                  {Object.entries(layers).map(([key, layer]) => {
                    const layerKey = key as keyof typeof layers;
                    const layerData: Record<
                      string,
                      { name: string; color: string; icon: string }
                    > = {
                      rainfall: {
                        name: "Rainfall",
                        color: "#2563EB",
                        icon: "rainy",
                      },
                      "soil-moisture": {
                        name: "Soil Moisture",
                        color: "#16A34A",
                        icon: "water_drop",
                      },
                      runoff: {
                        name: "Runoff",
                        color: "#EA580C",
                        icon: "waves",
                      },
                    };
                    const data = layerData[layerKey];
                    if (!data) return null;

                    return (
                      <div
                        key={layerKey}
                        className="rounded-[16px] border border-slate-900/6 bg-slate-50/60 p-3.5"
                      >
                        <div className="flex items-center justify-between">
                          <div
                            className="flex items-center gap-3 cursor-pointer transition-transform duration-180 ease-out hover:translate-x-[2px]"
                            onClick={() => toggleLayer(layerKey)}
                          >
                            <IconContainer color={`${data.color}14`}>
                              <span
                                className="material-symbols-rounded"
                                style={{
                                  fontSize: 24,
                                  color: data.color,
                                  opacity: layer.enabled ? 1 : 0.4,
                                }}
                              >
                                {data.icon}
                              </span>
                            </IconContainer>
                            <span
                              className={`text-sm font-medium transition-colors duration-200 ${
                                layer.enabled ? "text-slate-800" : "text-slate-400"
                              }`}
                            >
                              {data.name}
                            </span>
                          </div>
                          <div onClick={(e) => e.stopPropagation()}>
                            <Toggle
                              checked={layer.enabled}
                              onChange={() => toggleLayer(layerKey)}
                            />
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

export default DataExplorer;
