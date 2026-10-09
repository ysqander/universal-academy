import { Icon } from "../../components/Icon";
import { guidedStages, guidedStepLabel, guidedThen } from "../../lib/guided";
import { scoreView, type TaskEnd } from "../../lib/score";
import type { TaskProgress } from "../../types";
import { ScoreMeter } from "./ScoreMeter";

interface GuidedStepperProps {
  progress: TaskProgress;
  /** The score meter's figures: the latest answer's, while its feedback is still on screen. */
  scored?: TaskProgress;
  /** That answer finished the task: how it ended. */
  end?: TaskEnd | null;
}

/**
 * Progress of a proof lesson with scored steps (SPEC §6.3b): one segment per proof step, then the variant problem;
 * "Proof step 3 of 5 · then a variant problem (counts double)" while reading, "Final problem · A variant" while it is
 * answered, and the score so far against the 80 % pass mark (the steps count like questions).
 */
export function GuidedStepper({ progress, scored, end }: GuidedStepperProps) {
  const stages = guidedStages(progress);
  const inVariant = Boolean(progress.in_capstone);
  const then = guidedThen(progress);
  const view = scoreView(scored ?? progress, scored ? end : null);
  const label = inVariant ? "Final problem: a variant, counts double" : guidedStepLabel(progress);

  return (
    <div className="kp-stepper guided-stepper" aria-label={label}>
      <ol className="kp-stepper__segments" aria-hidden="true">
        {stages.map((stage) => (
          <li key={stage.key} data-state={stage.state} data-capstone={stage.capstone || undefined} />
        ))}
      </ol>
      <div className="kp-stepper__line">
        {inVariant ? (
          <>
            <span className="kp-stepper__count kp-stepper__count--capstone">
              <Icon name="layers" size={13} /> Final problem
            </span>
            <span className="kp-stepper__title">A variant</span>
            <span className="kp-stepper__question">Counts double</span>
          </>
        ) : (
          <span className="kp-stepper__count">
            {label}
            {then && <span className="kp-stepper__then"> · {then}</span>}
          </span>
        )}
      </div>
      <ScoreMeter view={view} />
    </div>
  );
}
