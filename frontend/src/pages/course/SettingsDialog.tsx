import { useId, useState } from "react";
import { getSettings, putSettings, type Settings } from "../../api";
import { Dialog } from "../../components/Dialog";
import { Spinner } from "../../components/Spinner";
import { useToast } from "../../context/toast";
import { useResource } from "../../hooks/useResource";

const PRESETS = [15, 20, 30, 45, 60];

// SPEC §6.7: quiz time per question is 60 s (choice questions) or 90 s (typed answers) × quiz_time_factor.
const QUIZ_TIMES: { factor: number; label: string; detail: string }[] = [
  { factor: 1, label: "Normal", detail: "60–90 s a question" },
  { factor: 1.5, label: "+50 %", detail: "90–135 s a question" },
  { factor: 2, label: "Double", detail: "2–3 min a question" },
];

/** The preset closest to a stored factor (an older server sends none: normal). */
const nearestFactor = (value: number | undefined): number =>
  QUIZ_TIMES.reduce((best, t) => (Math.abs(t.factor - (value ?? 1)) < Math.abs(best - (value ?? 1)) ? t.factor : best), 1);

interface SettingsDialogProps {
  open: boolean;
  /** The daily goal known to the page (shown at once; the server's settings fill in the rest). */
  currentGoal: number;
  onClose: () => void;
  onSaved: (settings: Settings) => void;
}

/** Study settings: the daily XP goal and the quiz time accommodation. */
export function SettingsDialog({ open, currentGoal, onClose, onSaved }: SettingsDialogProps) {
  return (
    <Dialog open={open} onClose={onClose} title="Study settings" size="sm">
      {open && <SettingsForm currentGoal={currentGoal} onClose={onClose} onSaved={onSaved} />}
    </Dialog>
  );
}

function SettingsForm({ currentGoal, onClose, onSaved }: Omit<SettingsDialogProps, "open">) {
  const settings = useResource("settings", (signal) => getSettings(signal));
  if (!settings.data && !settings.error) {
    return (
      <div className="settings-form settings-form--loading">
        <Spinner size={16} label="Loading settings" />
      </div>
    );
  }
  return (
    <SettingsFields
      goal={settings.data?.daily_goal ?? currentGoal}
      factor={nearestFactor(settings.data?.quiz_time_factor)}
      onClose={onClose}
      onSaved={onSaved}
    />
  );
}

function SettingsFields({
  goal: initialGoal,
  factor: initialFactor,
  onClose,
  onSaved,
}: {
  goal: number;
  factor: number;
  onClose: () => void;
  onSaved: (settings: Settings) => void;
}) {
  const toast = useToast();
  const inputId = useId();
  const quizName = useId();
  const [value, setValue] = useState(String(initialGoal));
  const [factor, setFactor] = useState(initialFactor);
  const [saving, setSaving] = useState(false);
  const goal = Number(value);
  const valid = Number.isInteger(goal) && goal >= 5 && goal <= 240;

  const save = async () => {
    if (!valid) return;
    setSaving(true);
    try {
      const saved = await putSettings({ daily_goal: goal, quiz_time_factor: factor });
      onSaved(saved);
      onClose();
    } catch (err) {
      toast.error(err);
      setSaving(false);
    }
  };

  return (
    <form
      className="goal-form settings-form"
      onSubmit={(event) => {
        event.preventDefault();
        void save();
      }}
    >
      <section className="settings-form__section" aria-labelledby={`${inputId}-goal`}>
        <h3 id={`${inputId}-goal`} className="settings-form__title">
          Daily goal
        </h3>
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
      </section>

      <fieldset className="settings-form__section settings-form__quiz">
        <legend className="settings-form__title">Quiz time</legend>
        <p className="muted">
          Quizzes are timed. If reading or typing math takes you longer, give yourself more time — it applies to any quiz whose clock hasn't started yet.
        </p>
        <div className="quiz-time">
          {QUIZ_TIMES.map((t) => (
            <label key={t.factor} className="quiz-time__option" data-checked={factor === t.factor || undefined}>
              <input type="radio" name={quizName} value={t.factor} checked={factor === t.factor} onChange={() => setFactor(t.factor)} />
              <span className="quiz-time__label">{t.label}</span>
              <span className="quiz-time__detail">{t.detail}</span>
            </label>
          ))}
        </div>
      </fieldset>

      <div className="goal-form__actions">
        <button type="button" className="btn btn--ghost" onClick={onClose}>
          Cancel
        </button>
        <button type="submit" className="btn btn--primary" disabled={!valid || saving}>
          {saving && <Spinner size={14} label="Saving" />} Save
        </button>
      </div>
    </form>
  );
}
