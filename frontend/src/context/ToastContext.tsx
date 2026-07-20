import React, { createContext, useContext, useState, useCallback } from "react";
import { X, CheckCircle2, AlertCircle, Info, HelpCircle } from "lucide-react";

export type ToastType = "success" | "error" | "info" | "confirm";
export type ToastPosition = "bottom-right" | "top-center" | "center";

interface ToastAction {
  label: string;
  onClick: () => void;
}

interface Toast {
  id: string;
  message: string;
  description?: string;
  type: ToastType;
  position: ToastPosition;
  action?: ToastAction;
  cancelAction?: ToastAction;
}

interface ToastOptions {
  description?: string;
  type?: ToastType;
  duration?: number;
  position?: ToastPosition;
  action?: ToastAction;
  cancelAction?: ToastAction;
}

interface ToastContextProps {
  toast: (message: string, options?: ToastOptions) => void;
}

const ToastContext = createContext<ToastContextProps | undefined>(undefined);

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const removeToast = useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const toast = useCallback(
    (message: string, options?: ToastOptions) => {
      const id = Math.random().toString(36).substring(2, 9);
      const type = options?.type || "info";
      const description = options?.description;
      const duration = options?.duration ?? (type === "confirm" ? 0 : 4000);
      const position = options?.position || "bottom-right";
      const action = options?.action;
      const cancelAction = options?.cancelAction;

      setToasts((prev) => [...prev, { id, message, description, type, position, action, cancelAction }]);

      if (duration > 0) {
        setTimeout(() => removeToast(id), duration);
      }
    },
    [removeToast]
  );

  const groupedToasts = {
    "bottom-right": toasts.filter((t) => t.position === "bottom-right"),
    "top-center": toasts.filter((t) => t.position === "top-center"),
    "center": toasts.filter((t) => t.position === "center"),
  };

  const renderToasts = (toastList: Toast[], slideInClass: string) => {
    return toastList.map((t) => (
      <div
        key={t.id}
        className={`pointer-events-auto flex w-full flex-col gap-3 overflow-hidden rounded-xl border border-slate-200 bg-white p-3.5 shadow-xl shadow-slate-900/5 backdrop-blur-xl transition-all duration-300 animate-in fade-in ${slideInClass}`}
      >
        <div className="flex w-full items-start gap-3">
          {/* Contextual Status Icon */}
          <div className="mt-0.5 shrink-0">
            {t.type === "success" && <CheckCircle2 size={16} className="text-emerald-500" />}
            {t.type === "error" && <AlertCircle size={16} className="text-rose-500" />}
            {t.type === "info" && <Info size={16} className="text-cyan-500" />}
            {t.type === "confirm" && <HelpCircle size={16} className="text-amber-500" />}
          </div>

          {/* Messaging text blocks */}
          <div className="flex-1">
            <p className="text-sm font-semibold text-slate-900">{t.message}</p>
            {t.description && (
              <p className="mt-1 text-[13px] leading-relaxed text-slate-500">{t.description}</p>
            )}
          </div>

          {/* Close Button Trigger */}
          <button
            type="button"
            onClick={() => removeToast(t.id)}
            className="mt-0.5 shrink-0 rounded p-0.5 text-slate-400 hover:bg-slate-100 hover:text-slate-700 transition"
          >
            <X size={13} />
          </button>
        </div>

        {/* Action Buttons for Confirm Toasts */}
        {(t.action || t.cancelAction) && (
          <div className="mt-1 flex gap-2 pl-7">
            {t.cancelAction && (
              <button
                type="button"
                onClick={() => {
                  t.cancelAction?.onClick();
                  removeToast(t.id);
                }}
                className="flex-1 rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-semibold text-slate-600 transition hover:bg-slate-50"
              >
                {t.cancelAction.label}
              </button>
            )}
            {t.action && (
              <button
                type="button"
                onClick={() => {
                  t.action?.onClick();
                  removeToast(t.id);
                }}
                className="flex-1 rounded-lg bg-cyan-500 px-3 py-1.5 text-xs font-semibold text-white shadow-sm transition hover:bg-cyan-400"
              >
                {t.action.label}
              </button>
            )}
          </div>
        )}
      </div>
    ));
  };

  return (
    <ToastContext.Provider value={{ toast }}>
      {children}
      
      {/* Toast Portals */}
      <div className="pointer-events-none fixed bottom-4 right-4 z-50 flex w-full max-w-sm flex-col gap-2 p-4 sm:bottom-6 sm:right-6">
        {renderToasts(groupedToasts["bottom-right"], "slide-in-from-bottom-4")}
      </div>
      
      <div className="pointer-events-none fixed top-4 left-1/2 z-50 flex w-full max-w-sm -translate-x-1/2 flex-col gap-2 p-4 sm:top-6">
        {renderToasts(groupedToasts["top-center"], "slide-in-from-top-4")}
      </div>
      
      <div className="pointer-events-none fixed top-1/2 left-1/2 z-50 flex w-full max-w-sm -translate-x-1/2 -translate-y-1/2 flex-col gap-2 p-4">
        {renderToasts(groupedToasts["center"], "zoom-in-95")}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast() {
  const context = useContext(ToastContext);
  if (!context) throw new Error("useToast must be used within a ToastProvider");
  return context;
}
