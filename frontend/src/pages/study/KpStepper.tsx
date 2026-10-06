import { Icon } from "../../components/Icon";
import { Markdown } from "../../components/Markdown";
import { hasCapstone, kpPosition, lessonStages, scoreView, type TaskEnd } from "../../lib/score";
import type { KnowledgePoint, TaskProgress } from "../../types";
import { ScoreMeter } from "./ScoreMeter";

interface KpStepperProps {
  points: KnowledgePoint[];
  progress: TaskProgress;
  /** The score meter's figures: the latest answer's, while its feedback is still on screen. */
  scored?: TaskProgress;
  /** That answer finished the task: how it ended (a halted lesson says "Stopped after n questions"). */
  end?: TaskEnd | null;
}

/**
 * Lesson progress: one segment per knowledge point (+ the capstone, "Put it together"), "Step 2 of 3 · title" with
 * "Question n of target" for the knowledge point, and the score so far against the 80 % pass mark.
 */
export function KpStepper({ points, progress, scored, end }: KpStepperProps) {
  const capstone = hasCapstone(progress);
  const stages = lessonStages(progress, points.length, capstone);
  const kpTotal = stages.filter((s) => !s.capstone).length;
  const inCapstone = Boolean(progress.in_capstone);
  const current = points[progress.kp_index];
  const position = kpPosition(progress, current);
  const view = scoreView(scored ?? progress, scored ? end : null);
  const label = inCapstone ? "Final problem: put it together" : `Knowledge point ${progress.kp_index + 1} of ${kpTotal}`;

  return (
    <div className="kp-stepper" aria-label={label}>
      <ol className="kp-stepper__segments" aria-hidden="true">
        {stages.map((stage) => (
          <li key={stage.key} data-state={stage.state} data-capstone={stage.capstone || undefined} />
        ))}
      </ol>
      <div className="kp-stepper__line">
        {inCapstone ? (
          <>
            <span className="kp-stepper__count kp-stepper__count--capstone">
              <Icon name="layers" size={13} /> Final problem
            </span>
            <span className="kp-stepper__title">Put it together</span>
            <span className="kp-stepper__question">Counts double</span>
          </>
        ) : (
          <>
            <span className="kp-stepper__count">
              Step {progress.kp_index + 1} of {kpTotal}
              {capstone && <span className="kp-stepper__then"> + final problem</span>}
            </span>
            {current && (
              <span className="kp-stepper__title">
                <Markdown inline>{current.title}</Markdown>
              </span>
            )}
            <span className="kp-stepper__question" data-extra={position.extra || undefined}>
              {position.label}
            </span>
          </>
        )}
      </div>
      <ScoreMeter view={view} />
    </div>
  );
}
