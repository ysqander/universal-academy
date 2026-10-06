import { useId, useState } from "react";
import { flagQuestion } from "../../api";
import { Dialog } from "../../components/Dialog";
import { Spinner } from "../../components/Spinner";
import { useToast } from "../../context/toast";
import type { Task } from "../../types";

interface FlagDialogProps {
  open: boolean;
  questionId: string;
  onClose: () => void;
  onFlagged: (task: Task) => void;
  /** What happens to the question (default: practice — voided and replaced). */
  description?: string;
}

const REASONS = ["The answer marked correct is wrong", "The question is ambiguous", "It uses something not taught yet", "There's a typo in the math"];

/** "This question seems wrong": excludes the question from scoring and serves a replacement. */
const DEFAULT_DESCRIPTION = "The question is removed from scoring — any answer to it is voided — and you get a replacement.";

export function FlagDialog({ open, questionId, onClose, onFlagged, description = DEFAULT_DESCRIPTION }: FlagDialogProps) {
  const toast = useToast();
  const id = useId();
  const [reason, setReason] = useState("");
  const [sending, setSending] = useState(false);

  const send = async () => {
    setSending(true);
    try {
      const task = await flagQuestion(questionId, reason.trim());
      onClose();
      toast.show("Thanks — that question is set aside and won't count.", "success");
      onFlagged(task);
    } catch (err) {
      toast.error(err);
    } finally {
      setSending(false);
    }
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Report this question"
      size="sm"
      footer={
        <>
          <button type="button" className="btn btn--ghost" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="btn btn--primary" onClick={() => void send()} disabled={sending}>
            {sending && <Spinner size={14} label="Sending" />} Set it aside
          </button>
        </>
      }
    >
      <div className="flag-form">
        <p className="muted">{description}</p>
        <div className="flag-form__reasons" role="group" aria-label="Common reasons">
          {REASONS.map((r) => (
            <button key={r} type="button" className="btn btn--sm" aria-pressed={reason === r} onClick={() => setReason(r)}>
              {r}
            </button>
          ))}
        </div>
        <label className="field" htmlFor={id}>
          <span className="field__label">What seems wrong? (optional)</span>
          <textarea id={id} className="textarea" rows={3} value={reason} onChange={(e) => setReason(e.target.value)} maxLength={500} />
        </label>
      </div>
    </Dialog>
  );
}
