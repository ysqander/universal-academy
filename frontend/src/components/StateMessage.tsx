import type { ReactNode } from "react";
import { errorMessage } from "../api";
import { Icon, type IconName } from "./Icon";
import { Spinner } from "./Spinner";
import "./StateMessage.css";

interface StateMessageProps {
  icon?: IconName;
  title: ReactNode;
  children?: ReactNode;
  actions?: ReactNode;
  tone?: "neutral" | "danger";
  compact?: boolean;
}

/** Centered empty / error / informational state. */
export function StateMessage({ icon = "info", title, children, actions, tone = "neutral", compact = false }: StateMessageProps) {
  return (
    <div className={`state-message state-message--${tone}${compact ? " state-message--compact" : ""}`}>
      <span className="state-message__icon">
        <Icon name={icon} size={compact ? 20 : 26} />
      </span>
      <h3 className="state-message__title">{title}</h3>
      {children && <div className="state-message__body">{children}</div>}
      {actions && <div className="state-message__actions">{actions}</div>}
    </div>
  );
}

export function ErrorState({ error, onRetry, title = "Couldn't load this" }: { error: unknown; onRetry?: () => void; title?: string }) {
  return (
    <StateMessage
      icon="warning"
      tone="danger"
      title={title}
      actions={
        onRetry && (
          <button type="button" className="btn" onClick={onRetry}>
            Try again
          </button>
        )
      }
    >
      {errorMessage(error)}
    </StateMessage>
  );
}

export function LoadingState({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="loading-state" aria-live="polite">
      <Spinner size={18} label={label} />
      <span>{label}</span>
    </div>
  );
}
