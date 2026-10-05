import { useLayoutEffect, useRef, useState } from "react";
import { getJob, resumeBuild } from "../../api";
import { Icon } from "../../components/Icon";
import { ProgressBar } from "../../components/ProgressBar";
import { Spinner } from "../../components/Spinner";
import { useToast } from "../../context/toast";
import { useResource } from "../../hooks/useResource";
import { isActiveState, isJobActive } from "../../lib/job";
import type { JobStage, JobState, JobStatus } from "../../types";
import "./BuildProgress.css";

const JOB_POLL_MS = 1500;

interface BuildProgressProps {
  courseId: string;
  initialJob: JobStatus | null;
  /** Called when the job leaves the running state (done / failed / interrupted) or is resumed. */
  onChange: (job: JobStatus) => void;
  /** Skills whose lesson failed to generate (a finished build can still have some; resuming retries only those). */
  failedLessons?: number;
}

function StageIcon({ stage }: { stage: JobStage }) {
  switch (stage.status) {
    case "done":
      return <Icon name="check" size={16} strokeWidth={2.25} />;
    case "running":
      return <Spinner size={14} label="Running" />;
    case "failed":
      return <Icon name="x" size={16} strokeWidth={2.25} />;
    case "skipped":
      return <span className="stage__dash" aria-hidden="true" />;
    case "pending":
      return <span className="stage__dot" aria-hidden="true" />;
  }
}

const STATUS_TEXT: Record<JobStage["status"], string> = {
  pending: "Waiting",
  running: "In progress",
  done: "Done",
  failed: "Failed",
  skipped: "Skipped",
};

export function BuildProgress({ courseId, initialJob, onChange, failedLessons = 0 }: BuildProgressProps) {
  const toast = useToast();
  const [resuming, setResuming] = useState(false);
  const [pollActive, setPollActive] = useState(initialJob === null || isJobActive(initialJob));
  const lastState = useRef<JobState | null>(initialJob?.state ?? null);
  const onChangeRef = useRef(onChange);
  useLayoutEffect(() => {
    onChangeRef.current = onChange;
  });

  // Each poll decides whether to keep polling and reports the moment the job stops running.
  const job = useResource(
    `job:${courseId}`,
    async (signal) => {
      const latest = await getJob(courseId, signal);
      const wasActive = isActiveState(lastState.current);
      lastState.current = latest.state;
      setPollActive(isJobActive(latest));
      if (wasActive && !isJobActive(latest)) onChangeRef.current(latest);
      return latest;
    },
    { pollMs: pollActive ? JOB_POLL_MS : null },
  );
  const status = job.data ?? initialJob;

  const resume = async () => {
    setResuming(true);
    try {
      const next = await resumeBuild(courseId);
      job.mutate(next);
      lastState.current = next.state;
      setPollActive(true);
      onChange(next);
    } catch (err) {
      toast.error(err);
    } finally {
      setResuming(false);
    }
  };

  if (!status) {
    return (
      <section className="build card" aria-busy="true">
        <div className="build__head">
          <Spinner size={18} label="Loading build status" />
          <h2 className="build__title">Checking the build…</h2>
        </div>
      </section>
    );
  }

  const active = isJobActive(status);
  const stopped = status.state === "failed" || status.state === "interrupted";
  const doneCount = status.stages.filter((s) => s.status === "done" || s.status === "skipped").length;
  // The stage being worked on (or where a stopped build halted — the one resuming starts with) names the step;
  // its position in the list is the stage number, not the count of finished stages.
  const findStage = (wanted: JobStage["status"]) => status.stages.findIndex((s) => s.status === wanted);
  let currentIndex = findStage("running");
  if (currentIndex < 0) currentIndex = findStage("failed");
  if (currentIndex < 0 && stopped) currentIndex = findStage("pending");
  const current = currentIndex >= 0 ? status.stages[currentIndex] : undefined;
  const stageNumber = Math.min(current ? currentIndex + 1 : Math.max(doneCount + (active ? 1 : 0), 1), status.stages.length);
  // A finished build whose failed lessons can be retried (the course is usable meanwhile).
  const retryLessons = status.state === "done" && failedLessons > 0;
  const lessonsWord = failedLessons === 1 ? "lesson" : "lessons";

  return (
    <section className="build card" data-state={retryLessons ? "partial" : status.state} aria-labelledby="build-heading" aria-live="polite">
      <div className="build__head">
        {active ? <Spinner size={18} label="Building" /> : stopped || retryLessons ? <Icon name="warning" size={20} /> : <Icon name="check" size={20} />}
        <div className="build__heading">
          <h2 id="build-heading" className="build__title">
            {status.state === "queued"
              ? "Waiting to start the build…"
              : active
                ? "Building your course"
                : status.state === "interrupted"
                  ? "The build was interrupted"
                  : status.state === "failed"
                    ? "The build stopped"
                    : retryLessons
                      ? `Course built — ${failedLessons} ${lessonsWord} failed`
                      : "Course built"}
          </h2>
          <p className="build__sub">
            {retryLessons ? (
              `You can study everything else meanwhile. Retrying writes only the missing ${lessonsWord}.`
            ) : (
              <>
                Stage {stageNumber} of {status.stages.length}
                {current ? ` · ${current.label}` : ""}
                {active ? " · the map appears as soon as the chapter is mapped; lessons fill in as they are written." : ""}
                {stopped ? " · resuming skips everything already finished." : ""}
              </>
            )}
          </p>
        </div>
        {(stopped || retryLessons) && (
          <button type="button" className="btn btn--primary" onClick={() => void resume()} disabled={resuming}>
            {resuming ? <Spinner size={14} label="Resuming" /> : <Icon name={stopped ? "play" : "review"} size={14} />}
            {stopped ? "Resume build" : `Retry failed lessons (${failedLessons})`}
          </button>
        )}
      </div>

      {status.error && (
        <div className="callout callout--danger build__error" role="alert">
          <Icon name="warning" size={18} />
          <span>{status.error}</span>
        </div>
      )}

      {!retryLessons && (
      <ol className="stages">
        {status.stages.map((stage) => (
          <li key={stage.key} className="stage" data-status={stage.status}>
            <span className="stage__icon">
              <StageIcon stage={stage} />
            </span>
            <div className="stage__body">
              <div className="stage__line">
                <span className="stage__label">{stage.label}</span>
                <span className="stage__status">
                  {stage.total > 0 ? `${stage.done}/${stage.total}` : STATUS_TEXT[stage.status]}
                </span>
              </div>
              {stage.total > 0 && stage.status !== "pending" && (
                <ProgressBar
                  value={stage.done}
                  max={stage.total}
                  label={`${stage.label} progress`}
                  size="sm"
                  tone={stage.status === "done" ? "learned" : stage.status === "failed" ? "due" : "accent"}
                />
              )}
              {stage.message && <p className="stage__message">{stage.message}</p>}
            </div>
          </li>
        ))}
      </ol>
      )}

      {status.log.length > 0 && (
        <details className="disclosure build__log">
          <summary>Build log ({status.log.length} lines)</summary>
          <div className="disclosure__body">
            <pre>{status.log.join("\n")}</pre>
          </div>
        </details>
      )}
    </section>
  );
}
