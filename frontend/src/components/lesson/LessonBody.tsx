import { useMemo } from "react";
import { FigureCatalogContext } from "../../context/course";
import { figureIdsIn, localFigureId, prettyTitle } from "../../lib/markdown";
import { kindLabel } from "../../lib/format";
import type { Lesson } from "../../types";
import { Icon } from "../Icon";
import { Markdown } from "../Markdown";
import { SourceRefs } from "../SourceRefs";
import { BookFigure, VisualizationView } from "../VisualizationView";
import { WorkedExampleView } from "./WorkedExampleView";
import "./Lesson.css";

interface LessonBodyProps {
  lesson: Lesson;
  kind?: string;
  /** Preview mode also lists every knowledge point (read-only) and the connection. */
  preview?: boolean;
}

/** The lesson introduction: objective → why it matters → the idea → notation → visual → depth on demand. */
export function LessonBody({ lesson, kind, preview = false }: LessonBodyProps) {
  // Keyed by the local id ("p21-f1") that [[figure:…]] placeholders use (older responses carry "<course>-p21-f1").
  const catalog = useMemo(() => Object.fromEntries(lesson.figures.map((f) => [localFigureId(f.id), f])), [lesson.figures]);
  const viz = lesson.visualization;
  const showViz = Boolean(viz && (viz.format !== "none" || viz.figure));
  const extraFigures = useMemo(() => {
    const used = new Set([...figureIdsIn(lesson.idea), ...figureIdsIn(lesson.why_it_matters)]);
    // VisualizationView always shows the planner's book figure (also next to a diagram / table / steps).
    if (lesson.visualization?.figure) used.add(localFigureId(lesson.visualization.figure.id));
    return lesson.figures.filter((f) => !used.has(localFigureId(f.id)));
  }, [lesson]);

  return (
    <FigureCatalogContext.Provider value={catalog}>
      <article className="lesson">
        <header className="lesson__header">
          <p className="kicker">
            Lesson · <span className="key">{lesson.skill_key}</span>
            {kind ? ` · ${kindLabel(kind)}` : ""}
          </p>
          <h1 className="lesson__title">
            <Markdown inline>{prettyTitle(lesson.title)}</Markdown>
          </h1>
          <div className="lesson__objective">
            <Icon name="target" size={18} />
            <div>
              <span className="lesson__objective-label">Objective</span>
              <Markdown inline>{lesson.objective}</Markdown>
            </div>
          </div>
        </header>

        <div className="lesson__grid">
          <div className="lesson__main lesson__main--first">
            {lesson.why_it_matters && (
              <section className="lesson__section">
                <h2 className="lesson__h">Why this matters</h2>
                <Markdown>{lesson.why_it_matters}</Markdown>
              </section>
            )}
            {lesson.idea && (
              <section className="lesson__section">
                <h2 className="lesson__h">The idea</h2>
                <Markdown>{lesson.idea}</Markdown>
              </section>
            )}
          </div>

          <aside className="lesson__aside" aria-label="Notation and sources">
            {lesson.notation.length > 0 && (
              <section className="margin-note">
                <h2 className="margin-note__title">Notation</h2>
                <dl className="notation">
                  {lesson.notation.map((item, i) => (
                    <div key={i} className="notation__row">
                      <dt>
                        <Markdown inline>{item.symbol}</Markdown>
                      </dt>
                      <dd>
                        <Markdown inline>{item.meaning}</Markdown>
                      </dd>
                    </div>
                  ))}
                </dl>
              </section>
            )}
            <SourceRefs refs={lesson.source_refs} pages={lesson.source_pages} labels={lesson.page_labels} compact />
          </aside>

          <div className="lesson__main lesson__main--rest">
            {viz && showViz && (
              <section className="lesson__section">
                <h2 className="lesson__h">See it</h2>
                <VisualizationView viz={viz} />
              </section>
            )}
            {extraFigures.length > 0 && (
              <section className="lesson__section">
                <h2 className="lesson__h">From the book</h2>
                <div className="lesson__figures">
                  {extraFigures.map((figure) => (
                    <BookFigure key={figure.id} figure={figure} />
                  ))}
                </div>
              </section>
            )}
            {lesson.missing_steps.trim() && (
              <details className="disclosure">
                <summary>Show the missing steps</summary>
                <div className="disclosure__body">
                  <Markdown>{lesson.missing_steps}</Markdown>
                </div>
              </details>
            )}
            {lesson.common_mistakes.length > 0 && (
              <details className="disclosure">
                <summary>Common mistakes ({lesson.common_mistakes.length})</summary>
                <div className="disclosure__body">
                  <ul className="lesson__mistakes">
                    {lesson.common_mistakes.map((mistake, i) => (
                      <li key={i}>
                        <Markdown>{mistake}</Markdown>
                      </li>
                    ))}
                  </ul>
                </div>
              </details>
            )}
            {preview && <KnowledgePointsPreview lesson={lesson} />}
          </div>
        </div>
      </article>
    </FigureCatalogContext.Provider>
  );
}

function KnowledgePointsPreview({ lesson }: { lesson: Lesson }) {
  return (
    <>
      {lesson.knowledge_points.map((kp) => (
        <section key={kp.index} className="lesson__section kp-preview">
          <p className="kicker">
            Knowledge point {kp.index + 1} of {lesson.knowledge_points.length} · {kp.question_count} practice questions
          </p>
          <h2 className="lesson__kp-title">
            <Markdown inline>{prettyTitle(kp.title)}</Markdown>
          </h2>
          <Markdown>{kp.explanation}</Markdown>
          <WorkedExampleView example={kp.worked_example} stepwise={false} />
        </section>
      ))}
      {lesson.connection && (
        <section className="lesson__section">
          <h2 className="lesson__h">Where this leads</h2>
          <Markdown>{lesson.connection}</Markdown>
        </section>
      )}
    </>
  );
}
