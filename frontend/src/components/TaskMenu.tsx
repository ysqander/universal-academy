import type { TaskOption } from "../types";
import { TASK_LABEL } from "../lib/format";
import { Icon } from "./Icon";
import { Markdown } from "./Markdown";
import { Spinner } from "./Spinner";
import "./TaskMenu.css";

const optionKey = (option: TaskOption): string => `${option.skill.id}:${option.type}`;

interface TaskMenuProps {
  options: TaskOption[];
  onStart: (option: TaskOption) => void;
  /** "<skillId>:<type>" of the option currently being started. */
  pendingKey: string | null;
  disabled?: boolean;
}

/** Math-Academy-style menu of up to five next tasks, each with a transparent reason. */
export function TaskMenu({ options, onStart, pendingKey, disabled = false }: TaskMenuProps) {
  return (
    <ol className="task-menu">
      {options.map((option, i) => {
        const key = optionKey(option);
        const pending = pendingKey === key;
        return (
          <li key={key} style={{ animationDelay: `${i * 45}ms` }}>
            <button
              type="button"
              className="task-option"
              data-type={option.type}
              onClick={() => onStart(option)}
              disabled={disabled || pendingKey !== null}
              aria-busy={pending || undefined}
            >
              <span className="task-option__icon" aria-hidden="true">
                {pending ? <Spinner size={16} label="Starting" /> : <Icon name={option.type === "lesson" ? "lesson" : "review"} size={18} />}
              </span>
              <span className="task-option__body">
                <span className="task-option__meta">
                  {TASK_LABEL[option.type]} · <span className="key">{option.skill.key}</span>
                </span>
                <span className="task-option__title">
                  <Markdown inline>{option.skill.title}</Markdown>
                </span>
                <span className="task-option__reason">{option.reason}</span>
              </span>
              <span className="task-option__xp" title="Experience points (1 XP ≈ 1 focused minute)">
                {option.xp_value} XP
              </span>
            </button>
          </li>
        );
      })}
    </ol>
  );
}
