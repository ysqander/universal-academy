import { plural } from "../../lib/format";
import { prettyTitle } from "../../lib/markdown";
import type { Lesson } from "../../types";
import { Markdown } from "../Markdown";
import { WorkedExampleView } from "./WorkedExampleView";

/** Lesson preview: every knowledge point (read-only, worked examples fully shown) and where the lesson leads.
 * The count is what practice asks for (the KP's target); the pool's extra questions come after a miss or in reviews.
 * A scored proof lesson sends the knowledge points' text only once its steps are answered: blank ones are left out. */
export function KnowledgePointsPreview({ lesson }: { lesson: Lesson }) {
  return (
    <>
      {lesson.knowledge_points.map((kp) => {
        const example = kp.worked_example;
        const hasExample = Boolean(example && (example.problem.trim() || example.steps.length > 0));
        if (!kp.explanation.trim() && !hasExample) return null;
        return (
          <section key={kp.index} className="lesson__section kp-preview">
            <p className="kicker">
              Knowledge point {kp.index + 1} of {lesson.knowledge_points.length} ·{" "}
              {plural(kp.target_questions || Math.min(kp.question_count, 2), "practice question")}
            </p>
            <h2 className="lesson__kp-title">
              <Markdown inline>{prettyTitle(kp.title)}</Markdown>
            </h2>
            {kp.explanation.trim() && <Markdown>{kp.explanation}</Markdown>}
            {hasExample && <WorkedExampleView example={example} stepwise={false} />}
          </section>
        );
      })}
      {lesson.connection && (
        <section className="lesson__section">
          <h2 className="lesson__h">Where this leads</h2>
          <Markdown>{lesson.connection}</Markdown>
        </section>
      )}
    </>
  );
}
