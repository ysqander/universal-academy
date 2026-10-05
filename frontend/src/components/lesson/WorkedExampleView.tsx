import { useState } from "react";
import type { WorkedExample } from "../../types";
import { Icon } from "../Icon";
import { Markdown } from "../Markdown";

interface WorkedExampleViewProps {
  example: WorkedExample;
  /** Reveal steps one at a time (study) or show everything (reference / preview). */
  stepwise?: boolean;
}

export function WorkedExampleView({ example, stepwise = true }: WorkedExampleViewProps) {
  const total = example.steps.length;
  const [shown, setShown] = useState(stepwise ? 0 : total);
  const done = shown >= total;

  return (
    <div className="worked">
      <div className="worked__label">
        <Icon name="lesson" size={14} /> Worked example
      </div>
      <Markdown className="worked__problem">{example.problem}</Markdown>
      {shown > 0 && (
        <ol className="worked__steps">
          {example.steps.slice(0, shown).map((step, i) => (
            <li key={i} className="worked__step">
              <span className="worked__num" aria-hidden="true">
                {i + 1}
              </span>
              <div className="worked__math">
                <Markdown>{step.math}</Markdown>
              </div>
              {step.why && (
                <div className="worked__why">
                  <Markdown>{step.why}</Markdown>
                </div>
              )}
            </li>
          ))}
        </ol>
      )}
      {done ? (
        example.takeaway && (
          <div className="worked__takeaway">
            <span className="worked__takeaway-label">Takeaway</span>
            <Markdown>{example.takeaway}</Markdown>
          </div>
        )
      ) : (
        <div className="worked__controls">
          <button type="button" className="btn btn--sm" onClick={() => setShown(shown + 1)}>
            {shown === 0 ? "Show the first step" : "Next step"}
            <span className="worked__count">
              {shown + 1}/{total}
            </span>
          </button>
          <button type="button" className="btn btn--quiet btn--sm" onClick={() => setShown(total)}>
            Show all
          </button>
        </div>
      )}
    </div>
  );
}
