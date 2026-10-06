import { useEffect, useMemo, useState } from "react";
import { setFoundationKnown } from "../../api";
import { Dialog } from "../../components/Dialog";
import { Icon } from "../../components/Icon";
import { Markdown } from "../../components/Markdown";
import { SourceRefs } from "../../components/SourceRefs";
import { Spinner } from "../../components/Spinner";
import { StatusChip } from "../../components/StatusChip";
import { useToast } from "../../context/toast";
import { cooldownEnd, returnTimeFromMessage, returnTimePhrase } from "../../lib/cooldown";
import { formatDateTime, kindLabel, percent, relativeTime } from "../../lib/format";
import { prettyTitle } from "../../lib/markdown";
import { quizContentLock } from "../../lib/quiz";
import type { CourseGraph, FoundationNode, SkillNode, Task } from "../../types";
import type { GraphSelection } from "./selection";
import "./SkillDrawer.css";

interface SkillDrawerProps {
  courseId: string;
  graph: CourseGraph;
  selection: GraphSelection | null;
  onSelect: (selection: GraphSelection) => void;
  onClose: () => void;
  /** `force`: "Start anyway" during the lesson's cooldown. */
  onLearn: (skill: SkillNode, options?: { force?: boolean }) => void;
  onReview: (skill: SkillNode) => void;
  onPreview: (skill: SkillNode) => void;
  onFoundationChange: (foundation: FoundationNode) => void;
  /** Key of the action currently starting ("<skillId>:lesson" / "<skillId>:review"). */
  pending: string | null;
  /** The open (unfinished) task, if any: its skill's button resumes it. While it is a quiz, lessons and the book are
   * closed (SPEC §6.7). */
  activeTask: Task | null;
  /** The server's 409 "comes back at …" per skill id, from a start refused during the lesson's cooldown. */
  cooldownMessages?: Record<string, string>;
  /** A quiz is due or open (SPEC §6.7): nothing else can be started — only an open lesson/review can be resumed. */
  quizFirst?: boolean;
}

/** Re-render when `until` (ms since the epoch) passes, so a cooldown ends on screen without a reload. */
function useNowUntil(until: number | null): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (until === null) return;
    const wait = Math.min(Math.max(until - Date.now(), 0) + 1000, 2 ** 31 - 1);
    const timer = window.setTimeout(() => setNow(Date.now()), wait);
    return () => window.clearTimeout(timer);
  }, [until]);
  return now;
}

/** Details of the selected node: a right-hand panel on desktop, a bottom sheet on phones. */
export function SkillDrawer(props: SkillDrawerProps) {
  const { graph, selection, onClose } = props;
  const skill = selection?.kind === "skill" ? graph.skills.find((s) => s.id === selection.id) : undefined;
  const foundation = selection?.kind === "foundation" ? graph.foundations.find((f) => f.key === selection.key) : undefined;
  const open = Boolean(skill || foundation);

  return (
    <Dialog
      open={open}
      onClose={onClose}
      variant="drawer"
      kicker={
        skill ? (
          <>
            <span className="key">{skill.key}</span> · {kindLabel(skill.kind)}
          </>
        ) : foundation ? (
          <>
            <span className="key">{foundation.key}</span> · Assumed knowledge
          </>
        ) : null
      }
      title={skill ? <Markdown inline>{prettyTitle(skill.title)}</Markdown> : foundation ? <Markdown inline>{prettyTitle(foundation.title)}</Markdown> : ""}
    >
      {skill && <SkillDetails key={skill.id} skill={skill} {...props} />}
      {foundation && <FoundationDetails key={foundation.key} foundation={foundation} {...props} />}
    </Dialog>
  );
}

function SkillRefList({ title, skills, onSelect }: { title: string; skills: SkillNode[]; onSelect: (s: GraphSelection) => void }) {
  if (!skills.length) return null;
  return (
    <section className="drawer-section">
      <h3 className="drawer-section__title">{title}</h3>
      <ul className="ref-list">
        {skills.map((s) => (
          <li key={s.id}>
            <button type="button" className="ref-chip" data-status={s.status} onClick={() => onSelect({ kind: "skill", id: s.id })}>
              <span className="ref-chip__dot" aria-hidden="true" />
              <span className="key">{s.key}</span>
              <span className="ref-chip__title">
                <Markdown inline>{prettyTitle(s.title)}</Markdown>
              </span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}

function Difficulty({ value }: { value: number }) {
  const level = Math.max(1, Math.min(5, Math.round(value)));
  return (
    <span className="difficulty" aria-label={`Difficulty ${level} of 5`} title={`Difficulty ${level} of 5`}>
      {Array.from({ length: 5 }, (_, i) => (
        <span key={i} className="difficulty__pip" data-on={i < level || undefined} />
      ))}
    </span>
  );
}

function SkillDetails({
  skill,
  graph,
  onSelect,
  onLearn,
  onReview,
  onPreview,
  pending,
  activeTask,
  cooldownMessages,
  quizFirst = false,
}: SkillDrawerProps & { skill: SkillNode }) {
  const byId = useMemo(() => new Map(graph.skills.map((s) => [s.id, s])), [graph.skills]);
  const prerequisites = graph.edges.filter((e) => e.target === skill.id).flatMap((e) => byId.get(e.source) ?? []);
  const unlocks = graph.edges.filter((e) => e.source === skill.id).flatMap((e) => byId.get(e.target) ?? []);
  const foundations = graph.foundations.filter((f) => skill.foundations.includes(f.key));
  const learned = skill.status === "learned" || skill.status === "mastered";
  const ready = skill.lesson_status === "ready";
  const missing = prerequisites.filter((p) => p.status !== "learned" && p.status !== "mastered");
  const sort = (list: SkillNode[]) => [...list].sort((a, b) => a.order_index - b.order_index);
  // A lesson started with "Learn anyway" keeps the skill locked (the menu won't offer it) but stays resumable.
  const openTask = activeTask !== null && activeTask.phase !== "done" ? activeTask : null;
  const resuming = openTask?.type === "lesson" && openTask.skill?.id === skill.id;
  // A due quiz blocks every other start; the open lesson or review of this skill may still be finished first.
  const resumingReview = openTask?.type === "review" && openTask.skill?.id === skill.id;
  const quizBlocked = quizFirst && !resuming && !resumingReview;
  const blockedTitle = quizBlocked ? "Take the quiz first" : undefined;
  // An open quiz (its intro too: it already lists the skills) closes the lesson preview and the book (SPEC §6.7).
  const contentLock = quizContentLock(openTask);
  const locked = skill.status === "locked" && !resuming;
  // In progress without an open task: the last attempt ended (halted, or set aside) — starting makes a new attempt.
  const learnLabel = resuming ? "Continue lesson" : skill.status === "in_progress" ? "Start again" : locked ? "Learn anyway" : "Learn this";
  // A halted lesson comes back after its cooldown with fresh questions (SPEC §6.3); "Start anyway" overrides it.
  const cooldownAt = skill.cooldown_until ? Date.parse(skill.cooldown_until) : Number.NaN;
  const now = useNowUntil(Number.isFinite(cooldownAt) ? cooldownAt : null);
  const coolingUntil = learned || resuming ? null : cooldownEnd(skill.cooldown_until, now);
  // An older server sends no cooldown_until: then only its 409 said so (and its time, if it named one).
  const refusedMessage = skill.cooldown_until === undefined && !learned && !resuming ? (cooldownMessages?.[skill.id] ?? null) : null;
  const backAt = coolingUntil !== null ? returnTimePhrase(coolingUntil, now) : refusedMessage ? returnTimeFromMessage(refusedMessage) : null;
  const cooling = coolingUntil !== null || refusedMessage !== null;

  return (
    <div className="skill-details">
      <div className="skill-details__status">
        <StatusChip status={skill.status} due={skill.due} />
        <span className="skill-details__facts">
          <Difficulty value={skill.difficulty} />
          <span>
            <Icon name="clock" size={14} /> ~{skill.est_minutes} min
          </span>
        </span>
      </div>

      <div className="skill-details__objective">
        <Icon name="target" size={16} />
        <Markdown inline>{skill.objective}</Markdown>
      </div>
      {skill.summary && <Markdown className="skill-details__summary">{skill.summary}</Markdown>}

      <div className="skill-details__actions">
        {cooling ? (
          <>
            <span className="skill-details__back" role="status">
              <Icon name="clock" size={16} /> {backAt ? `Back ${backAt}` : "Back later"}
            </span>
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              onClick={() => onLearn(skill, { force: true })}
              disabled={pending !== null || !ready || quizBlocked}
              title={blockedTitle ?? "Start a new attempt now instead of after the break"}
            >
              {pending === `${skill.id}:lesson` ? <Spinner size={14} label="Starting" /> : null}
              Start anyway
            </button>
          </>
        ) : learned ? (
          <button
            type="button"
            className="btn btn--primary"
            onClick={() => onReview(skill)}
            disabled={pending !== null || !ready || quizBlocked}
            title={blockedTitle}
          >
            {pending === `${skill.id}:review` ? <Spinner size={14} label="Starting" /> : <Icon name="review" size={16} />}
            {resumingReview ? "Continue review" : "Review now"}
          </button>
        ) : (
          <button
            type="button"
            className={`btn ${locked ? "" : "btn--primary"}`}
            onClick={() => onLearn(skill)}
            disabled={pending !== null || (!ready && !resuming) || quizBlocked}
            title={blockedTitle}
          >
            {pending === `${skill.id}:lesson` ? <Spinner size={14} label="Starting" /> : <Icon name="lesson" size={16} />}
            {learnLabel}
          </button>
        )}
        {ready && (
          <button
            type="button"
            className="btn btn--ghost"
            onClick={() => onPreview(skill)}
            disabled={contentLock !== null}
            title={contentLock?.lesson}
          >
            <Icon name={contentLock ? "lock" : "eye"} size={16} /> Preview lesson
          </button>
        )}
      </div>
      {(quizBlocked || contentLock) && (
        <p className="skill-details__note skill-details__note--quiz" role="status">
          <Icon name="stopwatch" size={14} />{" "}
          {quizBlocked && "Take the quiz first — it's waiting at the top of the course page. Reviews of anything you miss come straight after; then everything opens again. "}
          {contentLock?.note}
        </p>
      )}
      {cooling && (
        <p className="skill-details__note">
          The last attempt stopped below the 80% pass mark. This lesson comes back {backAt ?? "in about 2 hours"} with
          fresh questions — the break helps it settle.
        </p>
      )}
      {skill.lesson_status === "pending" && (
        <p className="skill-details__note">
          <Spinner size={12} label="Writing" /> The lesson for this skill is still being written.
        </p>
      )}
      {skill.lesson_status === "failed" && (
        <p className="skill-details__note skill-details__note--danger">
          <Icon name="warning" size={14} /> This lesson couldn't be generated. Use “Retry failed lessons” on the course page to try
          again.
        </p>
      )}
      {locked && !cooling && missing.length > 0 && ready && (
        <p className="skill-details__note">
          <Icon name="lock" size={14} /> Usually unlocked after {sort(missing).map((m) => m.key).join(", ")}. You can still start
          it — it will be harder without {missing.length === 1 ? "that skill" : "those skills"}.
        </p>
      )}
      {(resuming || skill.status === "in_progress") && missing.length > 0 && (
        <p className="skill-details__note">
          <Icon name="lock" size={14} /> Started ahead of {sort(missing).map((m) => m.key).join(", ")} — you can carry on; it may be
          harder without {missing.length === 1 ? "that skill" : "those skills"}.
        </p>
      )}

      {learned && (
        <section className="drawer-section">
          <h3 className="drawer-section__title">Memory</h3>
          <dl className="retention">
            <div>
              <dt>Estimated recall</dt>
              <dd>{skill.retrievability !== null ? percent(skill.retrievability) : "—"}</dd>
            </div>
            <div>
              <dt>Next review</dt>
              <dd title={skill.due_at ? formatDateTime(skill.due_at) : undefined}>
                {skill.due ? "Due now" : skill.due_at ? relativeTime(skill.due_at) : "—"}
              </dd>
            </div>
            <div>
              <dt>Stability</dt>
              <dd>{skill.stability_days !== null ? `${Math.round(skill.stability_days)} days` : "—"}</dd>
            </div>
            <div>
              <dt>Reviews</dt>
              <dd>{skill.reviews}</dd>
            </div>
          </dl>
        </section>
      )}

      <SkillRefList title="Builds on" skills={sort(prerequisites)} onSelect={onSelect} />
      <SkillRefList title="Unlocks" skills={sort(unlocks)} onSelect={onSelect} />

      {foundations.length > 0 && (
        <section className="drawer-section">
          <h3 className="drawer-section__title">Assumes you know</h3>
          <ul className="ref-list">
            {foundations.map((f) => (
              <li key={f.key}>
                <button type="button" className="ref-chip ref-chip--foundation" onClick={() => onSelect({ kind: "foundation", key: f.key })}>
                  <span className="key">{f.key}</span>
                  <span className="ref-chip__title">
                    <Markdown inline>{prettyTitle(f.title)}</Markdown>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="drawer-section">
        <SourceRefs refs={skill.source_refs} pages={skill.source_pages} labels={skill.page_labels} />
      </section>
    </div>
  );
}

function FoundationDetails({ courseId, foundation, graph, onSelect, onFoundationChange }: SkillDrawerProps & { foundation: FoundationNode }) {
  const toast = useToast();
  const [showAnswer, setShowAnswer] = useState(false);
  const [saving, setSaving] = useState<boolean | null>(null);
  const users = graph.skills.filter((s) => s.foundations.includes(foundation.key)).sort((a, b) => a.order_index - b.order_index);
  const refresher = foundation.known === false;

  const answer = async (known: boolean) => {
    setSaving(known);
    try {
      onFoundationChange(await setFoundationKnown(courseId, foundation.key, known));
      if (known) toast.show("Noted — you know this.", "success");
      else setShowAnswer(true);
    } catch (err) {
      toast.error(err);
    } finally {
      setSaving(null);
    }
  };

  return (
    <div className="skill-details">
      <p className="skill-details__lead">
        Knowledge from outside this chapter that the skills below rely on. It isn't taught here — check yourself quickly.
      </p>

      {refresher ? (
        <section className="foundation-refresher callout callout--info reveal" aria-labelledby={`refresher-${foundation.key}`}>
          <Icon name="review" size={18} />
          <div className="foundation-refresher__body">
            <h3 id={`refresher-${foundation.key}`} className="drawer-section__title">
              Refresher
            </h3>
            {foundation.description && <Markdown>{foundation.description}</Markdown>}
            {foundation.check_question && (
              <div className="foundation-refresher__example">
                <span className="foundation-check__label">Worked check</span>
                <Markdown>{foundation.check_question}</Markdown>
                {foundation.check_answer && (
                  <div className="foundation-check__answer">
                    <span className="foundation-check__label">Answer</span>
                    <Markdown>{foundation.check_answer}</Markdown>
                  </div>
                )}
              </div>
            )}
            <p className="muted foundation-refresher__note">
              Marked for a refresher. Read this before {users.length === 1 ? "the skill" : "the skills"} below; if it still feels
              shaky, your textbook's earlier chapters or appendix cover it in full.
            </p>
          </div>
        </section>
      ) : (
        <>
          {foundation.description && <Markdown>{foundation.description}</Markdown>}
          <section className="foundation-check">
            <h3 className="drawer-section__title">Quick check</h3>
            <Markdown>{foundation.check_question}</Markdown>
            {showAnswer ? (
              <div className="foundation-check__answer reveal">
                <span className="foundation-check__label">Answer</span>
                <Markdown>{foundation.check_answer}</Markdown>
              </div>
            ) : (
              <button type="button" className="btn btn--sm" onClick={() => setShowAnswer(true)}>
                <Icon name="eye" size={14} /> Show answer
              </button>
            )}
          </section>
        </>
      )}

      {foundation.known === true && (
        <p className="skill-details__note">
          <Icon name="check" size={14} /> You marked this as known.
        </p>
      )}

      <div className="skill-details__actions">
        <button
          type="button"
          className={`btn ${foundation.known === true ? "btn--primary" : ""}`}
          aria-pressed={foundation.known === true}
          onClick={() => void answer(true)}
          disabled={saving !== null}
        >
          {saving === true ? <Spinner size={14} label="Saving" /> : <Icon name="check" size={16} />} I know this
        </button>
        <button
          type="button"
          className={`btn ${foundation.known === false ? "btn--primary" : ""}`}
          aria-pressed={foundation.known === false}
          onClick={() => void answer(false)}
          disabled={saving !== null}
        >
          {saving === false ? <Spinner size={14} label="Saving" /> : <Icon name="review" size={16} />} I need a refresher
        </button>
      </div>

      <SkillRefList title="Used by" skills={users} onSelect={onSelect} />
    </div>
  );
}
