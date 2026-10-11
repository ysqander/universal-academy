import { scorePercent, type ScoreView } from "../../lib/score";

interface ScoreMeterProps {
  view: ScoreView;
  /** "Score 86% · pass at 80%" over the bar (default), or the completion screen's larger figure. */
  size?: "md" | "lg";
  /** Replaces the right-hand "3 of 7 answered". */
  aside?: string | null;
}

/** During a task: points earned out of the plan's points, the bar filling toward the pass line ("2 of 6 points · pass at
 * 5"). At the end: the score in percent with the pass mark drawn on the bar (SPEC §6.3: pass at 80 %). A lesson halted
 * early is marked as such ("Stopped after n questions"), never as a finished plan. */
export function ScoreMeter({ view, size = "md", aside }: ScoreMeterProps) {
  const pct = Math.round(view.fill * 1000) / 10;
  const mark = Math.round(view.passMark * 1000) / 10;
  const right = aside === undefined ? view.remainingLabel : aside;
  return (
    <div className="score-meter" data-tone={view.tone} data-size={size} data-halted={view.halted || undefined}>
      <div className="score-meter__line">
        <span className="score-meter__score">{view.label}</span>
        <span className="score-meter__pass">· {view.passLabel}</span>
        {right && <span className="score-meter__aside">{right}</span>}
      </div>
      <div
        className="score-meter__track"
        role="meter"
        aria-label="Score"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={view.hasScore ? scorePercent(view.fraction, view.passMark) : 0}
        aria-valuetext={view.ariaText}
      >
        <div className="score-meter__fill" style={{ width: `${pct}%` }} />
        <span className="score-meter__mark" style={{ left: `${mark}%` }} aria-hidden="true" />
      </div>
    </div>
  );
}
