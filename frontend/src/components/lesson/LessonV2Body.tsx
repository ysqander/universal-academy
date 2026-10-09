import { useEffect, useId, useLayoutEffect, useMemo, useReducer, useRef, useState, type ReactNode } from "react";
import { FigureCatalogContext } from "../../context/course";
import { kindLabel } from "../../lib/format";
import { isScoredCheck, isScoredLesson, moreSteps, type GuidedHandlers } from "../../lib/guided";
import {
  flowReducer,
  initialFlow,
  loadAnswered,
  saveAnswered,
  sequenceKey,
  stripGoalLabel,
  visibleBlocks,
  type CheckOutcome,
} from "../../lib/lessonFlow";
import { figureIdsIn, localFigureId, prettyTitle } from "../../lib/markdown";
import type { Lesson, NotationItem } from "../../types";
import { Icon } from "../Icon";
import { Markdown } from "../Markdown";
import { SourceRefs } from "../SourceRefs";
import { BookFigure } from "../VisualizationView";
import { KnowledgePointsPreview } from "./KnowledgePointsPreview";
import { TeachingBlock } from "./TeachingBlock";
import "./Lesson.css";
import "./LessonV2.css";

interface LessonV2BodyProps {
  lesson: Lesson;
  kind?: string;
  /** Lesson preview: nothing is gated (checks still work) and the knowledge points are listed read-only. */
  preview?: boolean;
  /** Shown after the whole sequence once no check holds it back (e.g. "Start practice"). */
  footer?: ReactNode;
  /** A proof lesson's scored steps are answered in this task (the study page); null/absent elsewhere. */
  guided?: GuidedHandlers | null;
}

/** A v2 lesson: a planned teaching sequence (blocks with a running example and inline checks) instead of fixed sections. */
export function LessonV2Body(props: LessonV2BodyProps) {
  const fingerprint = useMemo(() => sequenceKey(props.lesson, props.guided?.taskId), [props.lesson, props.guided?.taskId]);
  // A different lesson (or a regenerated one) starts with fresh gating state. A scored proof lesson keeps its key while
  // the server sends the next blocks (sequenceKey).
  return <Sequence key={fingerprint} fingerprint={fingerprint} {...props} />;
}

function Sequence({ lesson, kind, preview = false, footer, guided = null, fingerprint }: LessonV2BodyProps & { fingerprint: string }) {
  const gating = !preview;
  // Answered checks survive a reload within the browser session; skips don't (the gate closes again). Scored proof
  // steps are never stored here: their record comes from the server with the lesson.
  const [flow, dispatch] = useReducer(flowReducer, fingerprint, (fp: string) => initialFlow(gating ? loadAnswered(fp) : {}));
  const catalog = useMemo(() => Object.fromEntries(lesson.figures.map((f) => [localFigureId(f.id), f])), [lesson.figures]);
  // A scored proof lesson: the server sends the blocks up to the first open step; blocks_total counts them all.
  const total = Math.max(lesson.blocks_total || 0, lesson.blocks.length);
  const { shown, hidden, gate } = visibleBlocks(lesson.blocks, flow, gating, total);
  const scoredGate = gate !== null && isScoredCheck(gate.check);
  const notSent = isScoredLesson(lesson) ? total - lesson.blocks.length : 0;
  const blockRefs = useRef(new Map<number, HTMLElement>());
  const footerRef = useRef<HTMLDivElement>(null);
  // Blocks already on screen at first render don't fade in; later ones (revealed by a gate) do.
  const [initialShown] = useState(shown.length);
  const goal = stripGoalLabel(lesson.goal_line);

  useEffect(() => {
    if (gating) saveAnswered(fingerprint, flow.answered);
  }, [gating, fingerprint, flow.answered]);

  const extraFigures = useMemo(() => {
    const used = new Set(lesson.blocks.flatMap((b) => [...figureIdsIn(b.body), ...(b.visualization?.figure ? [localFigureId(b.visualization.figure.id)] : [])]));
    return lesson.figures.filter((f) => !used.has(localFigureId(f.id)));
  }, [lesson]);

  const onAnswered = (index: number, outcome: CheckOutcome) => dispatch({ type: "answered", index, outcome });
  const skip = () => {
    if (!gate) return;
    dispatch({ type: "skipped", index: gate.index });
    // Skipping removes the focused "Skip" link: once the revealed steps are on screen, focus the first of them.
    const next = lesson.blocks[lesson.blocks.indexOf(gate) + 1];
    requestAnimationFrame(() => {
      const target = next ? blockRefs.current.get(next.index) : footerRef.current;
      target?.focus({ preventScroll: true });
      target?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    });
  };

  const hasRefs = lesson.notation.length > 0 || lesson.source_refs.length > 0 || lesson.source_pages.length > 0;

  return (
    <FigureCatalogContext.Provider value={catalog}>
      <article className="lesson lesson-v2">
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
            <div className="lesson-v2__objective">
              <span className="lesson__objective-label">Objective</span>
              <Markdown inline>{lesson.objective}</Markdown>
              {goal && (
                <p className="lesson-v2__goal">
                  <span className="lesson-v2__goal-label">Your goal</span> <Markdown inline>{goal}</Markdown>
                </p>
              )}
            </div>
          </div>
          {(lesson.assumed.length > 0 || lesson.out_of_scope.length > 0) && (
            <div className="lesson-scope">
              {lesson.assumed.length > 0 && (
                <div className="lesson-scope__row">
                  <span className="lesson-scope__label">Builds on</span>
                  <ul className="lesson-scope__chips">
                    {lesson.assumed.map((item, i) => (
                      <li key={i} className="lesson-scope__chip">
                        <Markdown inline>{item}</Markdown>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {lesson.out_of_scope.length > 0 && <OutOfScope items={lesson.out_of_scope} />}
            </div>
          )}
        </header>

        {hasRefs && (
          <details className="disclosure lesson-v2__refs">
            <summary>{lesson.notation.length > 0 ? `Notation (${lesson.notation.length}) and sources` : "Sources"}</summary>
            <div className="disclosure__body">
              <NotationList notation={lesson.notation} />
              <SourceRefs refs={lesson.source_refs} pages={lesson.source_pages} labels={lesson.page_labels} compact />
            </div>
          </details>
        )}

        <div className="lesson__grid">
          <div className="lesson__main">
            <div className="teach-seq" data-gated={gate ? "" : undefined}>
              {shown.map((block, position) => (
                <TeachingBlock
                  key={block.index}
                  ref={(el) => {
                    if (el) blockRefs.current.set(block.index, el);
                    else blockRefs.current.delete(block.index);
                  }}
                  skillId={lesson.skill_id}
                  version={lesson.version ?? ""}
                  block={block}
                  outcome={flow.answered[block.index] ?? null}
                  skipped={Boolean(flow.skipped[block.index])}
                  onAnswered={onAnswered}
                  guided={guided}
                  fresh={position >= initialShown}
                />
              ))}
            </div>

            {gate ? (
              <div className="teach-gate" data-scored={scoredGate ? "" : undefined}>
                <p className="teach-gate__text">
                  <Icon name="lock" size={15} />
                  <span>
                    {/* A scored step has no Skip: it counts, and the server sends what follows once it is answered. */}
                    {scoredGate ? "Answer to continue — this step counts toward your score" : "Answer the check to continue"}
                    {hidden > 0 && <span className="teach-gate__count"> · {moreSteps(hidden)} after it</span>}
                  </span>
                </p>
                {!scoredGate && (
                  <button type="button" className="btn btn--quiet btn--sm teach-gate__skip" onClick={skip} aria-label="Skip the check and show the rest of the lesson">
                    Skip
                  </button>
                )}
              </div>
            ) : (
              <div className="lesson-v2__after">
                {/* The preview of a scored proof lesson: the server sends the blocks up to the first open step only. */}
                {notSent > 0 && (
                  <p className="teach-gate">
                    <span className="teach-gate__text">
                      <Icon name="lock" size={15} /> {moreSteps(notSent)} in the lesson
                    </span>
                  </p>
                )}
                {/* Figures of the blocks not sent yet would show out of context. */}
                {extraFigures.length > 0 && notSent === 0 && (
                  <section className="lesson__section">
                    <h2 className="lesson__h">From the book</h2>
                    <div className="lesson__figures">
                      {extraFigures.map((figure) => (
                        <BookFigure key={figure.id} figure={figure} />
                      ))}
                    </div>
                  </section>
                )}
                {lesson.deeper.trim() && (
                  <details className="disclosure lesson-deeper">
                    <summary>
                      Go deeper <span className="lesson-deeper__tag">Optional</span>
                    </summary>
                    <div className="disclosure__body">
                      <p className="lesson-deeper__note">Not needed for this lesson's goal or its practice — another angle, for the curious.</p>
                      <Markdown>{lesson.deeper}</Markdown>
                    </div>
                  </details>
                )}
                {preview && <KnowledgePointsPreview lesson={lesson} />}
                {footer && (
                  <div ref={footerRef} tabIndex={-1} className="lesson-v2__footer">
                    {footer}
                  </div>
                )}
              </div>
            )}
          </div>

          {hasRefs && (
            <aside className="lesson__aside" aria-label="Notation and sources">
              {lesson.notation.length > 0 && (
                <section className="margin-note">
                  <h2 className="margin-note__title">Notation</h2>
                  <NotationList notation={lesson.notation} />
                </section>
              )}
              <SourceRefs refs={lesson.source_refs} pages={lesson.source_pages} labels={lesson.page_labels} compact />
            </aside>
          )}
        </div>
      </article>
    </FigureCatalogContext.Provider>
  );
}

function NotationList({ notation }: { notation: NotationItem[] }) {
  if (notation.length === 0) return null;
  return (
    <dl className="notation">
      {notation.map((item, i) => (
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
  );
}

/** "Not in this lesson: …" on one line; a toggle shows the rest when it doesn't fit. */
function OutOfScope({ items }: { items: string[] }) {
  const listId = useId();
  const listRef = useRef<HTMLUListElement>(null);
  const [open, setOpen] = useState(false);
  const [overflows, setOverflows] = useState(false);

  useLayoutEffect(() => {
    const el = listRef.current;
    if (!el || open) return;
    const measure = () => setOverflows(el.scrollWidth > el.clientWidth + 1);
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, [items, open]);

  return (
    <div className="lesson-scope__row lesson-scope__row--out" data-open={open ? "" : undefined}>
      <span className="lesson-scope__label">Not in this lesson</span>
      <ul id={listId} ref={listRef} className="lesson-scope__out">
        {items.map((item, i) => (
          <li key={i}>
            <Markdown inline>{item}</Markdown>
          </li>
        ))}
      </ul>
      {(overflows || open) && (
        <button type="button" className="lesson-scope__toggle" aria-expanded={open} aria-controls={listId} onClick={() => setOpen(!open)}>
          {open ? "Less" : "Show all"}
        </button>
      )}
    </div>
  );
}
