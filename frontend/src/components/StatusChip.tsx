import type { SkillStatus } from "../types";
import { STATUS_LABEL } from "../lib/format";
import "./StatusChip.css";

interface StatusChipProps {
  status: SkillStatus;
  due?: boolean;
  size?: "sm" | "md";
}

export function StatusChip({ status, due = false, size = "md" }: StatusChipProps) {
  return (
    <span className={`status-chip status-chip--${size}`} data-status={status}>
      <span className="status-chip__dot" aria-hidden="true" />
      {STATUS_LABEL[status]}
      {due && <span className="status-chip__due">due</span>}
    </span>
  );
}
