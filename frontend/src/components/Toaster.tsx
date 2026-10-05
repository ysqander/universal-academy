import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { errorMessage } from "../api";
import { ToastContext, type Toast, type ToastApi, type ToastTone } from "../context/toast";
import { Icon } from "./Icon";
import "./Toaster.css";

const LIFETIME_MS = { info: 4500, success: 4000, error: 7000 } satisfies Record<ToastTone, number>;
const MAX_VISIBLE = 4;

const supportsPopover = (el: HTMLElement): boolean => typeof el.showPopover === "function";

const popoverOpen = (el: HTMLElement): boolean => {
  try {
    return el.matches(":popover-open");
  } catch {
    return false;
  }
};

export function Toaster({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const nextId = useRef(1);
  const regionRef = useRef<HTMLDivElement>(null);
  const shownId = useRef(0);

  // The toaster is a manual popover, so it lives in the top layer. A modal <dialog> (skill drawer, confirmations)
  // opened later sits above it, so a new toast re-opens the popover to come back on top — otherwise an error raised
  // from inside a drawer ("locked", "couldn't start") would render hidden behind it.
  useEffect(() => {
    const el = regionRef.current;
    if (!el || !supportsPopover(el)) return;
    const newest = toasts.at(-1)?.id ?? 0;
    const added = newest > shownId.current;
    shownId.current = Math.max(shownId.current, newest);
    try {
      if (!popoverOpen(el)) el.showPopover();
      else if (added && document.querySelector("dialog:modal")) {
        el.hidePopover();
        el.showPopover();
      }
    } catch {
      // Not connected yet, or the browser refused: the toaster still renders as a fixed element.
    }
  }, [toasts]);

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
      <div ref={regionRef} className="toaster" popover="manual" aria-live="polite" aria-relevant="additions">
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
