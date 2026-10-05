import { useEffect, useId, useRef, type MouseEvent, type ReactNode } from "react";
import { Icon } from "./Icon";
import "./Dialog.css";

interface DialogProps {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  /** Small line above the title (e.g. "S4 · Concept"). */
  kicker?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  /** "modal" is centred; "drawer" is a right-hand panel that becomes a bottom sheet on phones. */
  variant?: "modal" | "drawer";
  size?: "sm" | "md" | "lg" | "xl";
  className?: string;
}

/** Accessible dialog built on the native <dialog> (focus trap, Esc, top layer). */
export function Dialog({ open, onClose, title, kicker, children, footer, variant = "modal", size = "md", className }: DialogProps) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const onCloseRef = useRef(onClose);
  const openRef = useRef(open);

  useEffect(() => {
    onCloseRef.current = onClose;
  });

  useEffect(() => {
    openRef.current = open;
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);

  // The dialog can also be closed natively (e.g. form method="dialog"); report that to the owner once.
  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    const onNativeClose = () => {
      if (openRef.current) onCloseRef.current();
    };
    dialog.addEventListener("close", onNativeClose);
    return () => dialog.removeEventListener("close", onNativeClose);
  }, []);

  const onBackdropClick = (event: MouseEvent<HTMLDialogElement>) => {
    if (event.target === event.currentTarget) onClose();
  };

  return (
    <dialog
      ref={ref}
      className={`dialog dialog--${variant} dialog--${size}${className ? ` ${className}` : ""}`}
      aria-labelledby={titleId}
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      onClick={onBackdropClick}
    >
      {open && (
        <div className="dialog__panel">
          <header className="dialog__header">
            <div className="dialog__heading">
              {kicker && <div className="dialog__kicker">{kicker}</div>}
              <h2 id={titleId} className="dialog__title">
                {title}
              </h2>
            </div>
            <button type="button" className="btn btn--ghost btn--icon btn--sm dialog__close" onClick={onClose} aria-label="Close">
              <Icon name="x" size={18} />
            </button>
          </header>
          <div className="dialog__body">{children}</div>
          {footer && <footer className="dialog__footer">{footer}</footer>}
        </div>
      )}
    </dialog>
  );
}
