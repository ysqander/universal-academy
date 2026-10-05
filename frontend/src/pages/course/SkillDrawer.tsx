import { useMemo, useState } from "react";
import { setFoundationKnown } from "../../api";
import { Dialog } from "../../components/Dialog";
import { Icon } from "../../components/Icon";
import { Markdown } from "../../components/Markdown";
import { SourceRefs } from "../../components/SourceRefs";
import { Spinner } from "../../components/Spinner";
import { StatusChip } from "../../components/StatusChip";
import { useToast } from "../../context/toast";
import { formatDateTime, kindLabel, percent, relativeTime } from "../../lib/format";
import type { CourseGraph, FoundationNode, SkillNode } from "../../types";
import type { GraphSelection } from "./selection";
import "./SkillDrawer.css";

interface SkillDrawerProps {
  courseId: string;
  graph: CourseGraph;
  selection: GraphSelection | null;
  onSelect: (selection: GraphSelection) => void;
  onClose: () => void;
  onLearn: (skill: SkillNode) => void;
  onReview: (skill: SkillNode) => void;
  onPreview: (skill: SkillNode) => void;
  onFoundationChange: (foundation: FoundationNode) => void;
  /** Key of the action currently starting ("<skillId>:lesson" / "<skillId>:review"). */
  pending: string | null;
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
      title={skill ? <Markdown inline>{skill.title}</Markdown> : foundation ? <Markdown inline>{foundation.title}</Markdown> : ""}
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
                <Markdown inline>{s.title}</Markdown>
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

function SkillDetails({ skill, graph, onSelect, onLearn, onReview, onPreview, pending }: SkillDrawerProps & { skill: SkillNode }) {
  const byId = useMemo(() => new Map(graph.skills.map((s) => [s.id, s])), [graph.skills]);
  const prerequisites = graph.edges.filter((e) => e.target === skill.id).flatMap((e) => byId.get(e.source) ?? []);
  const unlocks = graph.edges.filter((e) => e.source === skill.id).flatMap((e) => byId.get(e.target) ?? []);
  const foundations = graph.foundations.filter((f) => skill.foundations.includes(f.key));
  const learned = skill.status === "learned" || skill.status === "mastered";
  const ready = skill.lesson_status === "ready";
  const missing = prerequisites.filter((p) => p.status !== "learned" && p.status !== "mastered");
  const sort = (list: SkillNode[]) => [...list].sort((a, b) => a.order_index - b.order_index);

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
        {learned ? (
          <button
            type="button"
            className="btn btn--primary"
            onClick={() => onReview(skill)}
            disabled={pending !== null || !ready}
          >
            {pending === `${skill.id}:review` ? <Spinner size={14} label="Starting" /> : <Icon name="review" size={16} />}
            Review now
          </button>
        ) : (
          <button
            type="button"
            className={`btn ${skill.status === "locked" ? "" : "btn--primary"}`}
            onClick={() => onLearn(skill)}
            disabled={pending !== null || !ready}
          >
            {pending === `${skill.id}:lesson` ? <Spinner size={14} label="Starting" /> : <Icon name="lesson" size={16} />}
            {skill.status === "locked" ? "Learn anyway" : skill.status === "in_progress" ? "Continue lesson" : "Learn this"}
          </button>
        )}
        {ready && (
          <button type="button" className="btn btn--ghost" onClick={() => onPreview(skill)}>
            <Icon name="eye" size={16} /> Preview lesson
          </button>
        )}
      </div>
      {skill.lesson_status === "pending" && (
        <p className="skill-details__note">
          <Spinner size={12} label="Writing" /> The lesson for this skill is still being written.
        </p>
      )}
      {skill.lesson_status === "failed" && (
        <p className="skill-details__note skill-details__note--danger">
          <Icon name="warning" size={14} /> This lesson couldn't be generated. Resuming the build retries it.
        </p>
      )}
      {skill.status === "locked" && missing.length > 0 && ready && (
        <p className="skill-details__note">
          <Icon name="lock" size={14} /> Usually unlocked after {missing.map((m) => m.key).join(", ")}. You can still start it.
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
                    <Markdown inline>{f.title}</Markdown>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      {skill.misconceptions.length > 0 && (
        <section className="drawer-section">
          <h3 className="drawer-section__title">Watch out for</h3>
          <ul className="misconceptions">
            {skill.misconceptions.map((m, i) => (
              <li key={i}>
                <Markdown inline>{m}</Markdown>
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

  const answer = async (known: boolean) => {
    setSaving(known);
    try {
      onFoundationChange(await setFoundationKnown(courseId, foundation.key, known));
      toast.show(known ? "Noted — you know this." : "Noted. Review it before the skills that rely on it.", "success");
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

      {foundation.known !== null && (
        <p className="skill-details__note">
          <Icon name={foundation.known ? "check" : "review"} size={14} />
          {foundation.known ? "You marked this as known." : "You asked for a refresher on this."}
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
