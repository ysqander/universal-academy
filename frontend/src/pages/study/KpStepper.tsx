import { Markdown } from "../../components/Markdown";
import type { KnowledgePoint, TaskProgress } from "../../types";

interface KpStepperProps {
  points: KnowledgePoint[];
  progress: TaskProgress;
}

/** "Step 2 of 3 · title" with segment bar and the two-in-a-row dots of the current knowledge point. */
export function KpStepper({ points, progress }: KpStepperProps) {
  const total = Math.max(progress.kp_total, points.length, 1);
  const current = points[progress.kp_index];
  const needed = Math.max(progress.needed_in_row, 1);
  return (
    <div className="kp-stepper" aria-label={`Knowledge point ${progress.kp_index + 1} of ${total}`}>
      <ol className="kp-stepper__segments" aria-hidden="true">
        {Array.from({ length: total }, (_, i) => (
          <li key={i} data-state={i < progress.kp_index ? "done" : i === progress.kp_index ? "current" : "todo"} />
        ))}
      </ol>
      <div className="kp-stepper__line">
        <span className="kp-stepper__count">
          Step {progress.kp_index + 1} of {total}
        </span>
        {current && (
          <span className="kp-stepper__title">
            <Markdown inline>{current.title}</Markdown>
          </span>
        )}
        <span className="kp-stepper__streak" title={`${needed} correct in a row without help passes this step`}>
          <span className="visually-hidden">
            {progress.streak} of {needed} in a row
          </span>
          {Array.from({ length: needed }, (_, i) => (
            <span key={i} className="kp-stepper__dot" data-on={i < progress.streak || undefined} aria-hidden="true" />
          ))}
          <span className="kp-stepper__streak-label" aria-hidden="true">
            in a row
          </span>
        </span>
      </div>
    </div>
  );
}
