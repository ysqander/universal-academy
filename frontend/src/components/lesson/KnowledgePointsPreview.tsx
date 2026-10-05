import { prettyTitle } from "../../lib/markdown";
import type { Lesson } from "../../types";
import { Markdown } from "../Markdown";
import { WorkedExampleView } from "./WorkedExampleView";

/** Lesson preview: every knowledge point (read-only, worked examples fully shown) and where the lesson leads. */
export function KnowledgePointsPreview({ lesson }: { lesson: Lesson }) {
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
