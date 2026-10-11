import type { ReactNode } from "react";
import { getLesson, isApiError } from "../../api";
import { useResource } from "../../hooks/useResource";
import { Dialog } from "../Dialog";
import { Icon } from "../Icon";
import { ErrorState, LoadingState, StateMessage } from "../StateMessage";
import { LessonBody } from "./LessonBody";
import "./LessonPreviewDialog.css";

interface LessonPreviewDialogProps {
  /** The skill whose lesson is shown (`kind` labels it); null = closed. */
  skill: { id: string; kind?: string } | null;
  onClose: () => void;
  title?: ReactNode;
  /** A line above the lesson, e.g. what opening it costs. */
  notice?: ReactNode;
  /** A footer button that closes the dialog with this label (e.g. "Back to the question"). */
  closeLabel?: string;
}

/** Read-only view of a skill's whole lesson (intro, knowledge points, worked examples). */
export function LessonPreviewDialog({ skill, onClose, title = "Lesson preview", notice, closeLabel }: LessonPreviewDialogProps) {
  const lesson = useResource(skill ? `lesson:${skill.id}` : null, (signal) => getLesson(skill?.id ?? "", signal));
  return (
    <Dialog
      open={skill !== null}
      onClose={onClose}
      size="xl"
      title={title}
      className="lesson-preview"
      footer={
        closeLabel ? (
          <button type="button" className="btn btn--primary" onClick={onClose}>
            <Icon name="arrowLeft" size={16} /> {closeLabel}
          </button>
        ) : undefined
      }
    >
      {notice && (
        <p className="lesson-preview__notice" role="note">
          <Icon name="info" size={16} /> <span>{notice}</span>
        </p>
      )}
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
