import { useCallback, useMemo, useRef, useState, type ReactNode } from "react";
import { errorMessage } from "../api";
import { ToastContext, type Toast, type ToastApi, type ToastTone } from "../context/toast";
import { Icon } from "./Icon";
import "./Toaster.css";

const LIFETIME_MS = { info: 4500, success: 4000, error: 7000 } satisfies Record<ToastTone, number>;
const MAX_VISIBLE = 4;

export function Toaster({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const nextId = useRef(1);

  const dismiss = useCallback((id: number) => setToasts((list) => list.filter((t) => t.id !== id)), []);

  const show = useCallback(
    (message: string, tone: ToastTone = "info") => {
      const id = nextId.current++;
      setToasts((list) => [...list.filter((t) => t.message !== message), { id, tone, message }].slice(-MAX_VISIBLE));
      window.setTimeout(() => dismiss(id), LIFETIME_MS[tone]);
    },
    [dismiss],
  );

  const api = useMemo<ToastApi>(() => ({ show, error: (err) => show(errorMessage(err), "error") }), [show]);

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div className="toaster" aria-live="polite" aria-relevant="additions">
        {toasts.map((toast) => (
          <div key={toast.id} className={`toast toast--${toast.tone}`} role={toast.tone === "error" ? "alert" : "status"}>
            <Icon name={toast.tone === "error" ? "warning" : toast.tone === "success" ? "check" : "info"} size={18} />
            <span className="toast__message">{toast.message}</span>
            <button type="button" className="toast__close" onClick={() => dismiss(toast.id)} aria-label="Dismiss">
              <Icon name="x" size={14} />
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}
