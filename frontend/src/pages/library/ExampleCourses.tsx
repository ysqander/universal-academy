import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { importExample, listExamples } from "../../api";
import { Icon } from "../../components/Icon";
import { Spinner } from "../../components/Spinner";
import { useToast } from "../../context/toast";
import { useResource } from "../../hooks/useResource";
import { plural } from "../../lib/format";
import { plainText, prettyTitle } from "../../lib/markdown";
import type { ExampleCourse } from "../../types";
import "./ExampleCourses.css";

/** Example courses shipped with the app (the repo's examples/ folder): ready to study, no API key needed. A card is
 * shown until its course is added; the course then appears under "Continue learning". */
export function ExampleCourses() {
  const examples = useResource("examples", listExamples);
  const available = (examples.data ?? []).filter((example) => !example.imported);
  if (available.length === 0) return null; // nothing to offer (or the list failed: the rest of the Library still works)

  return (
    <section className="library__section" aria-labelledby="examples-heading">
      <div className="section-title">
        <h2 id="examples-heading">Try an example course</h2>
      </div>
      <div className="course-grid">
        {available.map((example) => (
          <ExampleCard key={example.name} example={example} />
        ))}
      </div>
    </section>
  );
}

function ExampleCard({ example }: { example: ExampleCourse }) {
  const navigate = useNavigate();
  const toast = useToast();
  const [adding, setAdding] = useState(false);
  const title = plainText(prettyTitle(example.title));

  const add = async () => {
    setAdding(true);
    try {
      const result = await importExample(example.name);
      toast.show(result.message, "success");
      navigate(`/courses/${encodeURIComponent(result.course_id)}`);
    } catch (err) {
      toast.error(err);
      setAdding(false);
    }
  };

  return (
    <article className="course-card card example-card" data-status="example">
      <div className="course-card__top">
        <span className="course-card__state">
          <Icon name="book" size={14} /> Example course
        </span>
        <span className="course-card__pages">{plural(example.skills, "skill")}</span>
      </div>
      <h3 className="course-card__title">{title}</h3>
      <p className="course-card__book">{example.book_title}</p>
      {(example.author || example.license) && (
        <p className="example-card__credit">
          {example.author}
          {example.author && example.license && " · "}
          {example.license &&
            (example.license_url ? (
              <a href={example.license_url} target="_blank" rel="noreferrer">
                {example.license}
              </a>
            ) : (
              example.license
            ))}
        </p>
      )}
      {example.description && <p className="example-card__description">{example.description}</p>}
      <div className="course-card__actions">
        <button type="button" className="btn btn--primary" onClick={() => void add()} disabled={adding}>
          {adding ? <Spinner size={14} label="Adding" /> : <Icon name="plus" size={14} />}
          Add to my library
        </button>
      </div>
    </article>
  );
}
