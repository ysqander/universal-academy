import { useEffect, useLayoutEffect, useRef } from "react";

export type HotkeyHandler = (event: KeyboardEvent) => boolean | void;

const isTypingTarget = (target: EventTarget | null): boolean => {
  if (!(target instanceof HTMLElement)) return false;
  if (target.isContentEditable) return true;
  const tag = target.tagName;
  if (tag === "TEXTAREA" || tag === "SELECT") return true;
  if (tag !== "INPUT") return false;
  const type = (target as HTMLInputElement).type;
  return !["button", "checkbox", "radio", "range", "submit", "reset"].includes(type);
};

/**
 * Window-level single-key shortcuts (no modifiers). Ignored while the user types in a field
 * or while a modal dialog is open. Return `false` from a handler to let the event through.
 */
export function useHotkeys(bindings: Record<string, HotkeyHandler>, enabled = true): void {
  const ref = useRef(bindings);
  useLayoutEffect(() => {
    ref.current = bindings;
  });

  useEffect(() => {
    if (!enabled) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.metaKey || event.ctrlKey || event.altKey) return;
      if (isTypingTarget(event.target)) return;
      if (document.querySelector("dialog[open]")) return;
      const handler = ref.current[event.key.length === 1 ? event.key.toLowerCase() : event.key];
      if (handler && handler(event) !== false) event.preventDefault();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [enabled]);
}
