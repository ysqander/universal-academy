import { useState, type ReactNode } from "react";
import type { Figure, Visualization } from "../types";
import { useCourseContext } from "../context/course";
import { Icon } from "./Icon";
import { Markdown } from "./Markdown";
import { ComparisonTable } from "./viz/ComparisonTable";
import { Diagram2D } from "./viz/Diagram2D";
import { EquationSteps } from "./viz/EquationSteps";
import { FramesStepper } from "./viz/FramesStepper";
import { FunctionPlot } from "./viz/FunctionPlot";
import "./VisualizationView.css";

export function BookFigure({ figure }: { figure: Figure }) {
  const course = useCourseContext();
  const [failed, setFailed] = useState(false);
  return (
    <div className="viz-book-figure">
      {failed ? (
        <div className="viz-book-figure__missing">
          <Icon name="warning" size={16} /> This figure couldn't be loaded.
        </div>
      ) : (
        <img src={figure.url} alt={figure.description || figure.caption || "Figure from the book"} loading="lazy" onError={() => setFailed(true)} />
      )}
      <div className="viz-book-figure__meta">
        {figure.caption && <Markdown inline>{figure.caption}</Markdown>}
        {course && (
          <button type="button" className="btn btn--quiet btn--sm" onClick={() => course.openSource(figure.page)}>
            <Icon name="source" size={14} /> Book p. {figure.page_label}
          </button>
        )}
      </div>
    </div>
  );
}

function renderBody(viz: Visualization): ReactNode {
  switch (viz.format) {
    case "book_figure":
    case "none":
      return viz.figure ? <BookFigure figure={viz.figure} /> : null;
    case "comparison_table":
      return viz.table && viz.table.rows.length > 0 ? <ComparisonTable table={viz.table} /> : null;
    case "equation_steps":
      return viz.steps.length > 0 ? <EquationSteps steps={viz.steps} /> : null;
    case "diagram_2d":
      return viz.diagram ? <Diagram2D diagram={viz.diagram} title={viz.caption} /> : null;
    case "function_plot":
      return viz.plot_curves.length > 0 ? (
        <FunctionPlot
          curves={viz.plot_curves}
          xRange={viz.plot_x_range}
          yRange={viz.plot_y_range}
          parameter={viz.plot_parameter}
          title={viz.caption}
        />
      ) : null;
    case "frames":
      return viz.frames.length > 0 ? <FramesStepper frames={viz.frames} /> : null;
  }
}

/** Renders every visualization format the planner can choose; prompt above, caption below. */
export function VisualizationView({ viz }: { viz: Visualization }) {
  const body = renderBody(viz);
  // The planner may pair the book's own figure with a generated visual: show it alongside.
  const companion = viz.figure && viz.format !== "book_figure" && viz.format !== "none" ? <BookFigure figure={viz.figure} /> : null;
  if (!body && !companion) return null;
  return (
    <figure className="viz" data-format={viz.format}>
      {viz.learner_prompt && (
        <div className="viz__prompt">
          <Icon name="eye" size={16} />
          <Markdown inline>{viz.learner_prompt}</Markdown>
        </div>
      )}
      <div className="viz__body">
        {body}
        {companion}
      </div>
      {viz.caption && (
        <figcaption className="viz__caption">
          <Markdown inline>{viz.caption}</Markdown>
        </figcaption>
      )}
    </figure>
  );
}
