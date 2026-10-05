import { createContext, useContext } from "react";

export type ToastTone = "info" | "success" | "error";

export interface Toast {
  id: number;
  tone: ToastTone;
  message: string;
}

export interface ToastApi {
  show: (message: string, tone?: ToastTone) => void;
  error: (err: unknown) => void;
}

export const ToastContext = createContext<ToastApi | null>(null);

export function useToast(): ToastApi {
  const api = useContext(ToastContext);
  if (!api) throw new Error("useToast must be used inside <Toaster>");
  return api;
}
