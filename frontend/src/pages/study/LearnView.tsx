import { useMemo, useState } from "react";
import { flagGuided } from "../../api";
import { Icon } from "../../components/Icon";
import { LessonBody } from "../../components/lesson/LessonBody";
import { LessonV2Body } from "../../components/lesson/LessonV2Body";
import { Spinner } from "../../components/Spinner";
import { guidedStart, isScoredLesson, type GuidedHandlers } from "../../lib/guided";
import { hasCapstone, passMarkOf, percentLabel } from "../../lib/score";
import type { Lesson, Task } from "../../types";
import { FlagDialog } from "./FlagDialog";
import { GuidedStepper } from "./GuidedStepper";

interface LearnViewProps {
  task: Task;
  lesson: Lesson;
  onStart: () => void;
  starting: boolean;
  /** The server's task after a scored proof step (progress, the next blocks) or a report replaces the shown one. */
  onTask: (task: Task) => void;
}

const STEP_REASONS = ["The answer marked correct is wrong", "The step is ambiguous", "It uses something not taught yet", "There's a typo in the math"];

/** Lesson phase "learn": the introduction (v1) or the teaching sequence (v2), then a clear start into practice. A proof
 * lesson with scored steps (SPEC §6.3b) shows its score while they are answered, then offers the variant problem. */
export function LearnView({ task, lesson, onStart, starting, onTask }: LearnViewProps) {
  const kps = lesson.knowledge_points.length;
  const capstone = hasCapstone(task.progress);
  const scored = isScoredLesson(lesson);
  // The step whose answer is being reported as wrong (its points stop counting).
  const [flagIndex, setFlagIndex] = useState<number | null>(null);
  const guided = useMemo<GuidedHandlers | null>(() => (scored ? { taskId: task.id, onTask, onFlag: setFlagIndex } : null), [scored, task.id, onTask]);
  const variant = scored ? guidedStart(task.progress) : null;
  const start = variant ? (
    <div className="learn-view__start card" data-ends={variant.ends ? "" : undefined}>
      <div>
        <h2 className="learn-view__start-title">{variant.title}</h2>
        <p className="muted">
          {variant.text}
          {variant.ends ? "" : ` Worth ${task.xp_value} XP.`}
        </p>
      </div>
      <button type="button" className={`btn ${variant.ends ? "" : "btn--primary "}btn--lg`} onClick={onStart} disabled={starting}>
        {starting ? <Spinner size={16} label={variant.ends ? "Ending the attempt" : "Starting the variant problem"} /> : <Icon name={variant.ends ? "clock" : "play"} size={18} />}{" "}
        {variant.button}
      </button>
    </div>
  ) : (
    <div className="learn-view__start card">
      <div>
        <h2 className="learn-view__start-title">Ready to practise?</h2>
        <p className="muted">
          {kps > 0 ? `${kps} short ${kps === 1 ? "step" : "steps"}, each with a worked example${capstone ? ", then one problem that puts them together" : ""}. ` : ""}
          A right answer earns 1 point, ½ with a hint; {percentLabel(passMarkOf(task.progress))} passes. Worth {task.xp_value} XP.
        </p>
      </div>
      <button type="button" className="btn btn--primary btn--lg" onClick={onStart} disabled={starting}>
        {starting ? <Spinner size={16} label="Starting practice" /> : <Icon name="play" size={18} />} Start practice
      </button>
    </div>
  );
  return (
    <div className="learn-view">
      {scored && (
        <div className="learn-view__progress">
          <GuidedStepper progress={task.progress} />
        </div>
      )}
      {lesson.format === "v2" ? (
        // The start of practice sits behind the sequence's checks like any later step (a check can be skipped; a
        // scored proof step can't — the server sends what follows it once it is answered).
        <LessonV2Body lesson={lesson} footer={start} guided={guided} />
      ) : (
        <>
          <LessonBody lesson={lesson} />
          {start}
        </>
      )}
      {scored && (
        <FlagDialog
          open={flagIndex !== null}
          send={(reason) => flagGuided(task.id, flagIndex ?? -1, reason)}
          onClose={() => setFlagIndex(null)}
          onFlagged={onTask}
          title="Report this step"
          description="The step stops counting toward your score, whatever you answered, and the report is kept for a look at the lesson."
          reasons={STEP_REASONS}
          confirmLabel="Don't count it"
          sentMessage="Thanks — that step won't count toward your score."
        />
      )}
    </div>
  );
}
