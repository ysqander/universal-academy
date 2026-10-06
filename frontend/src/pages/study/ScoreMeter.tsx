import type { ScoreView } from "../../lib/score";

interface ScoreMeterProps {
  view: ScoreView;
  /** "Score 86% · pass at 80%" over the bar (default), or the completion screen's larger figure. */
  size?: "md" | "lg";
  /** Replaces the right-hand "3 of 7 answered". */
  aside?: string | null;
}

/** Score of the answered questions with the pass mark drawn on the bar (SPEC §6.3: pass at 80 %). */
export function ScoreMeter({ view, size = "md", aside }: ScoreMeterProps) {
  const pct = Math.round(view.fraction * 1000) / 10;
  const mark = Math.round(view.passMark * 1000) / 10;
  const right = aside === undefined ? view.remainingLabel : aside;
  return (
    <div className="score-meter" data-tone={view.tone} data-size={size}>
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
        aria-valuenow={view.hasScore ? Math.floor(pct) : 0}
        aria-valuetext={view.ariaText}
      >
        <div className="score-meter__fill" style={{ width: `${view.hasScore ? pct : 0}%` }} />
        <span className="score-meter__mark" style={{ left: `${mark}%` }} aria-hidden="true" />
      </div>
    </div>
  );
}
