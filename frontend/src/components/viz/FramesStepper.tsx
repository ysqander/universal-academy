import { useState } from "react";
import type { VisualizationFrame } from "../../types";
import { Icon } from "../Icon";
import { Markdown } from "../Markdown";
import { Tex } from "../Tex";
import { Diagram2D } from "./Diagram2D";

/** Ordered stills of a sequence or transformation, stepped through with prev / next. */
export function FramesStepper({ frames }: { frames: VisualizationFrame[] }) {
  const [index, setIndex] = useState(0);
  const frame = frames[Math.min(index, frames.length - 1)];
  if (!frame) return null;
  return (
    <div className="viz-frames">
      <div className="viz-frames__head">
        <span className="kicker">
          Step {index + 1} of {frames.length}
        </span>
        {frame.title && <Markdown inline className="viz-frames__title">{frame.title}</Markdown>}
      </div>
      <div className="viz-frames__stage" aria-live="polite">
        {frame.diagram && <Diagram2D diagram={frame.diagram} title={frame.title} />}
        {frame.latex && <Tex tex={frame.latex} display />}
        {frame.note && <Markdown className="viz-frames__note">{frame.note}</Markdown>}
      </div>
      <div className="viz-frames__nav">
        <button type="button" className="btn btn--sm" disabled={index === 0} onClick={() => setIndex(index - 1)}>
          <Icon name="chevronLeft" size={16} /> Previous
        </button>
        <div className="viz-frames__dots" role="group" aria-label="Choose a step">
          {frames.map((f, i) => (
            <button
              key={i}
              type="button"
              className="viz-frames__dot"
              aria-current={i === index ? "step" : undefined}
              aria-label={`Step ${i + 1}${f.title ? `: ${f.title}` : ""}`}
              onClick={() => setIndex(i)}
            />
          ))}
        </div>
        <button type="button" className="btn btn--sm" disabled={index === frames.length - 1} onClick={() => setIndex(index + 1)}>
          Next <Icon name="chevronRight" size={16} />
        </button>
      </div>
    </div>
  );
}
