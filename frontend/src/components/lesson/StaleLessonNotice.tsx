import { useEffect, useRef } from "react";
import { STALE_LESSON_MESSAGE } from "../../lib/feedback";
import { Icon } from "../Icon";

/**
 * The server answered 409: this lesson was regenerated since the page loaded it, so block numbers may now point at
 * other steps. Nothing on this page can be answered reliably any more; a reload shows the new version.
 */
export function StaleLessonNotice({ autoFocus = true }: { autoFocus?: boolean }) {
  const buttonRef = useRef<HTMLButtonElement>(null);

  // Shown in reply to an action whose control is now disabled: keep keyboard focus on the way forward.
  useEffect(() => {
    if (autoFocus) buttonRef.current?.focus({ preventScroll: true });
  }, [autoFocus]);

  return (
    <div className="stale-notice" role="alert">
      <Icon name="info" size={16} />
      <span className="stale-notice__text">{STALE_LESSON_MESSAGE}</span>
      <button ref={buttonRef} type="button" className="btn btn--sm stale-notice__reload" onClick={() => window.location.reload()}>
        Reload
      </button>
    </div>
  );
}
