import "./Spinner.css";

interface SpinnerProps {
  size?: number;
  label?: string;
  className?: string;
}

export function Spinner({ size = 16, label = "Loading", className }: SpinnerProps) {
  return (
    <span
      className={`spinner${className ? ` ${className}` : ""}`}
      style={{ width: size, height: size }}
      role="status"
      aria-label={label}
    />
  );
}
