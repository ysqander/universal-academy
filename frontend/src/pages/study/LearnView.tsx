import { Icon } from "../../components/Icon";
import { LessonBody } from "../../components/lesson/LessonBody";
import { LessonV2Body } from "../../components/lesson/LessonV2Body";
import { Spinner } from "../../components/Spinner";
import type { Lesson, Task } from "../../types";

interface LearnViewProps {
  task: Task;
  lesson: Lesson;
  onStart: () => void;
  starting: boolean;
}

/** Lesson phase "learn": the introduction (v1) or the teaching sequence (v2), then a clear start into practice. */
export function LearnView({ task, lesson, onStart, starting }: LearnViewProps) {
  const kps = lesson.knowledge_points.length;
  const start = (
    <div className="learn-view__start card">
      <div>
        <h2 className="learn-view__start-title">Ready to practise?</h2>
        <p className="muted">
          {kps > 0 ? `${kps} short ${kps === 1 ? "step" : "steps"}, each with a worked example. ` : ""}
          Two correct answers in a row — without hints — complete a step. Worth {task.xp_value} XP.
        </p>
      </div>
      <button type="button" className="btn btn--primary btn--lg" onClick={onStart} disabled={starting}>
        {starting ? <Spinner size={16} label="Starting practice" /> : <Icon name="play" size={18} />} Start practice
      </button>
    </div>
  );
  return (
    <div className="learn-view">
      {lesson.format === "v2" ? (
        // The start of practice sits behind the sequence's checks like any later step (a check can be skipped).
        <LessonV2Body lesson={lesson} footer={start} />
      ) : (
        <>
          <LessonBody lesson={lesson} />
          {start}
        </>
      )}
    </div>
  );
}
