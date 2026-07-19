import { useAppStore } from '../stores/useAppStore';
import { motion, AnimatePresence } from 'framer-motion';
import { Check, LoaderCircle } from 'lucide-react';
import type { LoadingScope } from '../stores/useAppStore';

// The ordered pipeline steps shown in the stepper.
// Each step matches a loadingPhase string emitted by the store.
const PIPELINE_STEPS: { label: string; description: string; phases: string[]; scopes: LoadingScope[] }[] = [
  {
    label: 'District Clipping',
    description: 'Aligning the district frame for the next raster pass',
    phases: ['Fetching district boundaries'],
    scopes: ['map'],
  },
  {
    label: 'Raster Fetching',
    description: 'Fetching and clipping the selected surface into district tiles',
    phases: [
      'Fetching ERA5-Land raster from local storage',
      'Clipping raster to district boundary',
      'Clipping raster across date range',
      'Rendering raster layer',
    ],
    scopes: ['map'],
  },
  {
    label: 'Collecting Data',
    description: 'Gathering required climate data records',
    phases: ['Collecting data'],
    scopes: ['analysis'],
  },
  {
    label: 'Precipitation Analysis',
    description: 'Rastering total precipitation snapshots for the selected window',
    phases: ['Fetching precipitation time series'],
    scopes: ['analysis'],
  },
  {
    label: 'Soil Moisture Analysis',
    description: 'Building soil moisture summaries from the fetched monthly surfaces',
    phases: ['Fetching soil moisture time series'],
    scopes: ['analysis'],
  },
  {
    label: 'Surface Runoff Analysis',
    description: 'Preparing surface runoff analysis outputs for the selected district',
    phases: ['Fetching surface runoff time series'],
    scopes: ['analysis'],
  },
];

const PHASE_COPY: Record<string, string> = {
  'Fetching district boundaries': 'Preparing district clipping frame',
  'Fetching ERA5-Land raster from local storage': 'Fetching monthly raster surfaces',
  'Clipping raster to district boundary': 'Clipping raster to the selected district',
  'Clipping raster across date range': 'Rastering and clipping the selected month range',
  'Rendering raster layer': 'Rendering clipped raster analysis',
  'Collecting data': 'Gathering required climate data records',
  'Fetching precipitation time series': 'Building TP analysis snapshots',
  'Fetching soil moisture time series': 'Building soil moisture analysis snapshots',
  'Fetching surface runoff time series': 'Building surface runoff analysis snapshots',
};

function isStepApplicable(scope: LoadingScope | null, stepScopes: LoadingScope[]): boolean {
  if (!scope) return true;
  return stepScopes.includes(scope);
}

export default function LoadingOverlay() {
  const loaderArmed = useAppStore((s) => s.loaderArmed);
  const phase = useAppStore((s) => s.loadingPhase);
  const loadingScope = useAppStore((s) => s.loadingScope);
  const isLoading = loaderArmed && phase !== null;
  const displayPhase = phase ? PHASE_COPY[phase] ?? 'Refreshing district analysis' : '';
  const visibleSteps = PIPELINE_STEPS.filter((step) =>
    isStepApplicable(loadingScope, step.scopes),
  );
  const visibleStepIndex = visibleSteps.findIndex((step) =>
    phase ? step.phases.includes(phase) : false,
  );

  return (
    <AnimatePresence>
      {isLoading && (
        <motion.div
          key="loading-overlay"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.2 }}
          className="fixed inset-0 z-[9999] flex items-center justify-center"
          style={{ background: 'rgba(248, 250, 252, 0.72)', backdropFilter: 'blur(4px)' }}
        >
          <motion.div
            initial={{ scale: 0.95, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            exit={{ scale: 0.95, opacity: 0 }}
            transition={{ duration: 0.2, delay: 0.05 }}
            className="w-full max-w-sm mx-4 rounded-2xl overflow-hidden"
            style={{
              background: 'rgba(255, 255, 255, 0.97)',
              border: '1px solid rgba(148,163,184,0.24)',
              boxShadow: '0 24px 72px rgba(15,23,42,0.14), 0 0 0 1px rgba(255,255,255,0.65)',
            }}
          >
            {/* Header */}
            <div className="px-6 pt-6 pb-4 border-b border-slate-200">
              <div className="flex items-center gap-3">
                <LoaderCircle
                  size={28}
                  className="animate-spin flex-shrink-0"
                  style={{ animationDuration: "0.45s" }}
                  color="#111111"
                />
                <div>
                  <p className="text-[13px] font-semibold text-slate-900 leading-tight">Refreshing District Analysis</p>
                  <p className="text-[11px] text-slate-500 mt-0.5 leading-tight">{displayPhase}</p>
                </div>
              </div>
            </div>

            {/* Steps */}
            <div className="px-6 py-4 space-y-0">
              {visibleSteps.map((step, i) => {
                const isActive = i === visibleStepIndex;
                const isDone = i < visibleStepIndex;
                const isPending = !isActive && !isDone;

                return (
                  <div key={step.label} className="flex gap-3">
                    {/* Connector column */}
                    <div className="flex flex-col items-center" style={{ width: 20, flexShrink: 0 }}>
                      {/* Circle indicator */}
                      <div
                        className="flex items-center justify-center rounded-full flex-shrink-0 transition-all duration-300"
                        style={{
                          width: 20,
                          height: 20,
                          marginTop: 10,
                          background: isDone
                            ? 'rgba(17,17,17,0.10)'
                            : isActive
                            ? 'rgba(17,17,17,0.08)'
                            : 'rgba(148,163,184,0.10)',
                          border: isDone
                            ? '1.5px solid rgba(17,17,17,0.65)'
                            : isActive
                            ? '1.5px solid rgba(17,17,17,0.8)'
                            : '1.5px solid rgba(148,163,184,0.22)',
                        }}
                      >
                        {isDone ? (
                          <Check size={10} strokeWidth={2.5} color="#111111" />
                        ) : isActive ? (
                          <LoaderCircle
                            size={12}
                            className="animate-spin"
                            style={{ animationDuration: "0.45s" }}
                            color="#111111"
                          />
                        ) : (
                          <div className="rounded-full" style={{ width: 5, height: 5, background: 'rgba(100,116,139,0.35)' }} />
                        )}
                      </div>
                      {/* Vertical line */}
                      {i < PIPELINE_STEPS.length - 1 && (
                        <div
                          className="w-px flex-1 transition-all duration-500"
                          style={{
                            background: isDone
                              ? 'rgba(17,17,17,0.32)'
                              : isActive
                              ? 'linear-gradient(to bottom, rgba(17,17,17,0.42), rgba(17,17,17,0.06))'
                              : 'rgba(148,163,184,0.18)',
                            minHeight: 16,
                          }}
                        />
                      )}
                    </div>

                    {/* Text */}
                    <div className="pb-4" style={{ paddingTop: 8 }}>
                      <p
                        className="text-[12px] font-medium leading-tight transition-colors duration-300"
                        style={{
                          color: isDone
                            ? 'rgba(15,23,42,0.92)'
                            : isActive
                            ? '#0f172a'
                            : isPending
                            ? 'rgba(51,65,85,0.78)'
                            : 'rgba(148,163,184,0.65)',
                        }}
                      >
                        {step.label}
                      </p>
                      {isActive && (
                        <motion.p
                          initial={{ opacity: 0, height: 0 }}
                          animate={{ opacity: 1, height: 'auto' }}
                          className="text-[10.5px] mt-0.5 leading-snug"
                          style={{ color: 'rgba(100,116,139,0.82)' }}
                        >
                          {step.description}
                        </motion.p>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
