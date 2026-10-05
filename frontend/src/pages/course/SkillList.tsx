import { useMemo } from "react";
import { Icon } from "../../components/Icon";
import { Markdown } from "../../components/Markdown";
import { Spinner } from "../../components/Spinner";
import { StatusChip } from "../../components/StatusChip";
import { kindLabel } from "../../lib/format";
import type { CourseGraph, SkillNode } from "../../types";
import type { GraphSelection } from "./selection";
import "./SkillList.css";

interface Group {
  id: string;
  title: string;
  skills: SkillNode[];
}

function groupSkills(skills: SkillNode[]): Group[] {
  const ordered = [...skills].sort((a, b) => a.order_index - b.order_index);
  const pick = (predicate: (s: SkillNode) => boolean) => ordered.filter(predicate);
  return [
    { id: "due", title: "Due for review", skills: pick((s) => s.due) },
    { id: "in_progress", title: "In progress", skills: pick((s) => !s.due && s.status === "in_progress") },
    { id: "available", title: "Ready to learn", skills: pick((s) => !s.due && s.status === "available") },
    { id: "learned", title: "Learned", skills: pick((s) => !s.due && s.status === "learned") },
    { id: "mastered", title: "Mastered", skills: pick((s) => !s.due && s.status === "mastered") },
    { id: "locked", title: "Locked — needs earlier skills first", skills: pick((s) => !s.due && s.status === "locked") },
  ].filter((g) => g.skills.length > 0);
}

interface SkillListProps {
  graph: CourseGraph;
  selection: GraphSelection | null;
  onSelect: (selection: GraphSelection) => void;
}

/** Skills in learning order, grouped by status — the default view on phones. */
export function SkillList({ graph, selection, onSelect }: SkillListProps) {
  const groups = useMemo(() => groupSkills(graph.skills), [graph.skills]);
  return (
    <div className="skill-list">
      {groups.map((group) => (
        <section key={group.id} className="skill-list__group" data-group={group.id} aria-labelledby={`group-${group.id}`}>
          <h3 id={`group-${group.id}`} className="skill-list__heading">
            {group.title} <span className="skill-list__count">{group.skills.length}</span>
          </h3>
          <ul className="skill-list__items">
            {group.skills.map((skill) => (
              <li key={skill.id}>
                <button
                  type="button"
                  className="skill-row"
                  data-status={skill.status}
                  aria-current={selection?.kind === "skill" && selection.id === skill.id ? "true" : undefined}
                  onClick={() => onSelect({ kind: "skill", id: skill.id })}
                >
                  <span className="skill-row__key">{skill.key}</span>
                  <span className="skill-row__main">
                    <span className="skill-row__title">
                      <Markdown inline>{skill.title}</Markdown>
                    </span>
                    <span className="skill-row__meta">
                      {kindLabel(skill.kind)} · ~{skill.est_minutes} min
                      {skill.lesson_status === "pending" && (
                        <span className="skill-row__pending">
                          <Spinner size={10} label="Lesson being written" /> lesson being written
                        </span>
                      )}
                      {skill.lesson_status === "failed" && <span className="skill-row__failed"> · lesson unavailable</span>}
                    </span>
                  </span>
                  <StatusChip status={skill.status} due={skill.due} size="sm" />
                  <Icon name="chevronRight" size={16} className="skill-row__chevron" />
                </button>
              </li>
            ))}
          </ul>
        </section>
      ))}
      {graph.foundations.length > 0 && (
        <section className="skill-list__group" aria-labelledby="group-foundations">
          <h3 id="group-foundations" className="skill-list__heading">
            Assumed knowledge <span className="skill-list__count">{graph.foundations.length}</span>
          </h3>
          <ul className="skill-list__items">
            {graph.foundations.map((f) => (
              <li key={f.key}>
                <button
                  type="button"
                  className="skill-row skill-row--foundation"
                  aria-current={selection?.kind === "foundation" && selection.key === f.key ? "true" : undefined}
                  onClick={() => onSelect({ kind: "foundation", key: f.key })}
                >
                  <span className="skill-row__key">{f.key}</span>
                  <span className="skill-row__main">
                    <span className="skill-row__title">
                      <Markdown inline>{f.title}</Markdown>
                    </span>
                    <span className="skill-row__meta">
                      {f.known === true ? "You know this" : f.known === false ? "Marked for a refresher" : "Quick self-check available"}
                    </span>
                  </span>
                  <Icon name="chevronRight" size={16} className="skill-row__chevron" />
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
