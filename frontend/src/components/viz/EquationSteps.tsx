import type { VizEqStep } from "../../types";
import { Markdown } from "../Markdown";
import { Tex } from "../Tex";

/** An algebraic argument as stacked display equations, each with a short note. */
export function EquationSteps({ steps }: { steps: VizEqStep[] }) {
  return (
    <ol className="viz-steps">
      {steps.map((step, i) => (
        <li key={i} className="viz-steps__item">
          <span className="viz-steps__num" aria-hidden="true">
            {i + 1}
          </span>
          <div className="viz-steps__body">
            <Tex tex={step.latex} display />
            {step.note && <Markdown className="viz-steps__note">{step.note}</Markdown>}
          </div>
        </li>
      ))}
    </ol>
  );
}
