import type { TaskOption } from "../types";
import { TASK_LABEL } from "../lib/format";
import { prettyTitle } from "../lib/markdown";
import { optionKey, optionTitle } from "../lib/quiz";
import { Icon } from "./Icon";
import { Markdown } from "./Markdown";
import { Spinner } from "./Spinner";
import { TASK_ICON } from "./taskIcon";
import "./TaskMenu.css";

interface TaskMenuProps {
  options: TaskOption[];
  onStart: (option: TaskOption) => void;
  /** Key of the option currently being started ("<skillId>:<type>", or "quiz:quiz" — see lib/quiz optionKey). */
  pendingKey: string | null;
  disabled?: boolean;
}

/** Math-Academy-style menu of up to five next tasks, each with a transparent reason. A due quiz is the only option. */
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
                {pending ? <Spinner size={16} label="Starting" /> : <Icon name={TASK_ICON[option.type]} size={18} />}
              </span>
              <span className="task-option__body">
                <span className="task-option__meta">
                  {TASK_LABEL[option.type]}
                  {option.skill ? (
                    <>
                      {" "}
                      · <span className="key">{option.skill.key}</span>
                    </>
                  ) : (
                    " · timed"
                  )}
                </span>
                <span className="task-option__title">
                  <Markdown inline>{prettyTitle(optionTitle(option))}</Markdown>
                </span>
                <span className="task-option__reason">{option.reason}</span>
              </span>
              <span className="task-option__xp" title="Experience points (1 XP ≈ 1 focused minute)">
                {option.type === "quiz" ? "up to " : ""}
                {option.xp_value} XP
              </span>
            </button>
          </li>
        );
      })}
    </ol>
  );
}
