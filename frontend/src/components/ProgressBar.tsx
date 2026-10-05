import "./ProgressBar.css";

interface ProgressBarProps {
  value: number;
  max: number;
  label: string;
  /** Visual tone of the filled part. */
  tone?: "accent" | "learned" | "progress" | "due";
  size?: "sm" | "md" | "lg";
  showValue?: boolean;
  className?: string;
}

export function ProgressBar({ value, max, label, tone = "accent", size = "md", showValue = false, className }: ProgressBarProps) {
  const fraction = max > 0 ? Math.min(1, Math.max(0, value / max)) : 0;
  return (
    <div className={`progress progress--${size} progress--${tone}${className ? ` ${className}` : ""}`}>
      <div
        className="progress__track"
        role="progressbar"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={max}
        aria-valuenow={Math.min(value, max)}
      >
        <div className="progress__fill" style={{ width: `${fraction * 100}%` }} />
      </div>
      {showValue && (
        <span className="progress__value">
          {value}/{max}
        </span>
      )}
    </div>
  );
}
