import { useEffect, useId, useRef, useState, type FormEvent, type KeyboardEvent, type Ref } from "react";
import { sendBlockFeedback } from "../../api";
import { useToast } from "../../context/toast";
import type { CheckOutcome } from "../../lib/lessonFlow";
import type { TeachingBlockView } from "../../types";
import { Icon } from "../Icon";
import { Markdown } from "../Markdown";
import { Spinner } from "../Spinner";
import { VisualizationView } from "../VisualizationView";
import { InlineCheck } from "./InlineCheck";

interface TeachingBlockProps {
  skillId: string;
  block: TeachingBlockView;
  outcome: CheckOutcome | null;
  skipped: boolean;
  onAnswered: (index: number, outcome: CheckOutcome) => void;
  /** Newly revealed after a gate opened: fade in. */
  fresh: boolean;
  ref?: Ref<HTMLElement>;
}

const CHECK_LABEL: Partial<Record<TeachingBlockView["kind"], string>> = {
  faded_example: "Your step",
};

function checkState(block: TeachingBlockView, outcome: CheckOutcome | null, skipped: boolean): string | undefined {
  if (!block.check) return undefined;
  if (outcome) return outcome.result.correct === false ? "missed" : "done";
  return skipped ? "skipped" : "open";
}

/** One step of a v2 teaching sequence: optional heading, text, visual and inline check, plus "This step isn't clear". */
export function TeachingBlock({ skillId, block, outcome, skipped, onAnswered, fresh, ref }: TeachingBlockProps) {
  const headingId = useId();
  const viz = block.visualization;
  const showViz = Boolean(viz && (viz.format !== "none" || viz.figure));
  return (
    <section
      ref={ref}
      tabIndex={-1}
      className={`teach-block${fresh ? " reveal" : ""}`}
      data-kind={block.kind}
      data-check={checkState(block, outcome, skipped)}
      aria-labelledby={block.heading ? headingId : undefined}
      aria-label={block.heading ? undefined : `Step ${block.index + 1}`}
    >
      <UnclearControl skillId={skillId} blockIndex={block.index} />
      <div className="teach-block__content">
        {block.kind === "faded_example" && (
          <p className="teach-block__label">
            <Icon name="steps" size={14} /> Your turn — finish the last step
          </p>
        )}
        {block.kind === "summary" && !block.heading && <p className="teach-block__label">In short</p>}
        {block.heading && (
          <h2 id={headingId} className="teach-block__h">
            <Markdown inline>{block.heading}</Markdown>
          </h2>
        )}
        <Markdown className="teach-block__body">{block.body}</Markdown>
        {viz && showViz && <VisualizationView viz={viz} />}
        {block.check && (
          <InlineCheck
            skillId={skillId}
            blockIndex={block.index}
            check={block.check}
            outcome={outcome}
            onAnswered={(next) => onAnswered(block.index, next)}
            label={CHECK_LABEL[block.kind] ?? (block.check.kind === "reflect" ? "Think about it" : "Quick check")}
          />
        )}
      </div>
    </section>
  );
}

type UnclearState = "idle" | "open" | "sending" | "sent";

/** "This step isn't clear": an icon in the block's margin (label on hover / focus), a text link on phones. */
function UnclearControl({ skillId, blockIndex }: { skillId: string; blockIndex: number }) {
  const toast = useToast();
  const formId = useId();
  const inputId = useId();
  const [state, setState] = useState<UnclearState>("idle");
  const [note, setNote] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const open = state === "open" || state === "sending";

  useEffect(() => {
    if (state === "open") inputRef.current?.focus({ preventScroll: true });
  }, [state]);

  const close = () => {
    setState("idle");
    triggerRef.current?.focus({ preventScroll: true });
  };

  const send = async (event: FormEvent) => {
    event.preventDefault();
    if (state !== "open") return;
    setState("sending");
    try {
      await sendBlockFeedback(skillId, blockIndex, { kind: "unclear", note: note.trim() });
      setState("sent");
      toast.show("Thanks — noted for this step", "success");
    } catch (err) {
      toast.error(err);
      setState("open");
    }
  };

  const onKeyDown = (event: KeyboardEvent<HTMLFormElement>) => {
    if (event.key === "Escape") {
      // Not the surrounding dialog (lesson preview): only this little form closes.
      event.preventDefault();
      event.stopPropagation();
      close();
    }
  };

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        className="unclear"
        data-state={state}
        aria-expanded={state === "sent" ? undefined : open}
        aria-controls={open ? formId : undefined}
        onClick={() => (open ? close() : setState("open"))}
        disabled={state === "sent"}
      >
        <Icon name={state === "sent" ? "check" : "help"} size={16} />
        <span className="unclear__label">{state === "sent" ? "Noted — thanks" : "This step isn't clear"}</span>
      </button>
      {open && (
        <form id={formId} className="unclear-form reveal" onSubmit={send} onKeyDown={onKeyDown}>
          <label htmlFor={inputId} className="unclear-form__label">
            What's unclear here? <span className="muted">(optional)</span>
          </label>
          <input
            ref={inputRef}
            id={inputId}
            className="input unclear-form__input"
            value={note}
            onChange={(event) => setNote(event.target.value)}
            maxLength={300}
            placeholder="e.g. where did the minus sign come from?"
            autoComplete="off"
            disabled={state === "sending"}
          />
          <div className="unclear-form__actions">
            <button type="button" className="btn btn--ghost btn--sm" onClick={close}>
              Cancel
            </button>
            <button type="submit" className="btn btn--primary btn--sm" disabled={state === "sending"}>
              {state === "sending" && <Spinner size={14} label="Sending" />} Send
            </button>
          </div>
        </form>
      )}
    </>
  );
}
