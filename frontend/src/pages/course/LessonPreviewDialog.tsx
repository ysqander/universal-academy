import { getLesson, isApiError } from "../../api";
import { Dialog } from "../../components/Dialog";
import { LessonBody } from "../../components/lesson/LessonBody";
import { ErrorState, LoadingState, StateMessage } from "../../components/StateMessage";
import { useResource } from "../../hooks/useResource";
import type { SkillNode } from "../../types";

interface LessonPreviewDialogProps {
  skill: SkillNode | null;
  onClose: () => void;
}

/** Read-only view of a skill's whole lesson (intro, knowledge points, worked examples). */
export function LessonPreviewDialog({ skill, onClose }: LessonPreviewDialogProps) {
  const lesson = useResource(skill ? `lesson:${skill.id}` : null, (signal) => getLesson(skill?.id ?? "", signal));
  return (
    <Dialog open={skill !== null} onClose={onClose} size="xl" title="Lesson preview" className="lesson-preview">
      {lesson.data ? (
        <LessonBody lesson={lesson.data} kind={skill?.kind} preview />
      ) : lesson.error ? (
        isApiError(lesson.error, 404) ? (
          <StateMessage icon="clock" title="This lesson isn't ready yet" compact>
            It is still being written. The map shows a spinner on skills whose lessons are pending.
          </StateMessage>
        ) : (
          <ErrorState error={lesson.error} onRetry={() => void lesson.reload()} />
        )
      ) : (
        <LoadingState label="Opening the lesson…" />
      )}
    </Dialog>
  );
}
