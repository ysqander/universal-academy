import { useId, useState } from "react";
import { putSettings } from "../../api";
import { Dialog } from "../../components/Dialog";
import { Spinner } from "../../components/Spinner";
import { useToast } from "../../context/toast";

const PRESETS = [15, 20, 30, 45, 60];

interface GoalDialogProps {
  open: boolean;
  current: number;
  onClose: () => void;
  onSaved: (goal: number) => void;
}

export function GoalDialog({ open, current, onClose, onSaved }: GoalDialogProps) {
  return (
    <Dialog open={open} onClose={onClose} title="Daily goal" size="sm">
      {open && <GoalForm current={current} onClose={onClose} onSaved={onSaved} />}
    </Dialog>
  );
}

function GoalForm({ current, onClose, onSaved }: Omit<GoalDialogProps, "open">) {
  const toast = useToast();
  const inputId = useId();
  const [value, setValue] = useState(String(current));
  const [saving, setSaving] = useState(false);
  const goal = Number(value);
  const valid = Number.isInteger(goal) && goal >= 5 && goal <= 240;

  const save = async () => {
    if (!valid) return;
    setSaving(true);
    try {
      const saved = await putSettings({ daily_goal: goal });
      onSaved(saved.daily_goal);
      onClose();
    } catch (err) {
      toast.error(err);
      setSaving(false);
    }
  };

  return (
    <form
      className="goal-form"
      onSubmit={(event) => {
        event.preventDefault();
        void save();
      }}
    >
      <p className="muted">One XP is about one focused minute. Around 30 XP a day, four days a week, is a steady pace for adults.</p>
      <div className="goal-form__presets" role="group" aria-label="Presets">
        {PRESETS.map((preset) => (
          <button
            key={preset}
            type="button"
            className="btn btn--sm"
            aria-pressed={goal === preset}
            data-active={goal === preset || undefined}
            onClick={() => setValue(String(preset))}
          >
            {preset} XP
          </button>
        ))}
      </div>
      <label className="field" htmlFor={inputId}>
        <span className="field__label">XP per day</span>
        <input
          id={inputId}
          className="input"
          inputMode="numeric"
          value={value}
          onChange={(event) => setValue(event.target.value)}
          aria-invalid={!valid || undefined}
        />
        {!valid && <span className="field__hint">Choose a whole number between 5 and 240.</span>}
      </label>
      <div className="goal-form__actions">
        <button type="button" className="btn btn--ghost" onClick={onClose}>
          Cancel
        </button>
        <button type="submit" className="btn btn--primary" disabled={!valid || saving}>
          {saving && <Spinner size={14} label="Saving" />} Save goal
        </button>
      </div>
    </form>
  );
}
