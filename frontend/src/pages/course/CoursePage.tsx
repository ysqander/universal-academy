import { useCallback, useState } from "react";
import { useNavigate } from "react-router-dom";
import { deleteCourse, getCourse, getGraph, getNext, isApiError } from "../../api";
import { CourseProvider } from "../../components/CourseProvider";
import { Dialog } from "../../components/Dialog";
import { Icon } from "../../components/Icon";
import { Markdown } from "../../components/Markdown";
import { Spinner } from "../../components/Spinner";
import { ErrorState, LoadingState, StateMessage } from "../../components/StateMessage";
import { TaskMenu } from "../../components/TaskMenu";
import { useToast } from "../../context/toast";
import { useDocumentTitle } from "../../hooks/useDocumentTitle";
import { NARROW_QUERY, useMediaQuery } from "../../hooks/useMediaQuery";
import { useResource } from "../../hooks/useResource";
import { useStartTask } from "../../hooks/useStartTask";
import type { FoundationNode, JobStatus, SkillNode, TaskOption } from "../../types";
import { isJobActive } from "../../lib/job";
import { plainText, prettyTitle } from "../../lib/markdown";
import { BuildProgress } from "./BuildProgress";
import { CourseHeader } from "./CourseHeader";
import { GoalDialog } from "./GoalDialog";
import { LessonPreviewDialog } from "./LessonPreviewDialog";
import type { GraphSelection } from "./selection";
import { SkillDrawer } from "./SkillDrawer";
import { SkillList } from "./SkillList";
import { SkillMap } from "./SkillMap";
import "./CoursePage.css";

const GRAPH_POLL_MS = 4000;
const COURSE_POLL_MS = 5000;

type View = "map" | "list";

const LEGEND: { status: string; label: string }[] = [
  { status: "locked", label: "Locked" },
  { status: "available", label: "Available" },
  { status: "in_progress", label: "In progress" },
  { status: "learned", label: "Learned" },
  { status: "mastered", label: "Mastered" },
  { status: "due", label: "Review due" },
  { status: "foundation", label: "Assumed knowledge" },
];

export default function CoursePage({ courseId }: { courseId: string }) {
  const navigate = useNavigate();
  const toast = useToast();
  const narrow = useMediaQuery(NARROW_QUERY);
  const [view, setView] = useState<View | null>(null);
  const [selection, setSelection] = useState<GraphSelection | null>(null);
  const [previewSkill, setPreviewSkill] = useState<SkillNode | null>(null);
  const [goalOpen, setGoalOpen] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [jobOverride, setJobOverride] = useState<JobStatus | null>(null);
  // Lessons the server refused to start because they are cooling down (409 "comes back at …"), by skill id: the drawer
  // offers "Start anyway" for them even when the graph doesn't carry cooldown_until (older server).
  const [cooldowns, setCooldowns] = useState<Record<string, string>>({});

  const course = useResource(`course:${courseId}`, (signal) => getCourse(courseId, signal));
  const job = jobOverride ?? course.data?.job ?? null;
  // The job is the source of truth: an interrupted or failed build is not "building" even while the course row says so.
  const building = job ? isJobActive(job) : course.data?.status === "building";
  const coursePolled = useResource(building ? `course-poll:${courseId}` : null, (signal) => getCourse(courseId, signal), {
    pollMs: COURSE_POLL_MS,
  });
  const courseData = coursePolled.data ?? course.data;

  const graph = useResource(course.data ? `graph:${courseId}` : null, (signal) => getGraph(courseId, signal), {
    pollMs: building ? GRAPH_POLL_MS : null,
  });
  const skillCount = graph.data?.skills.length ?? 0;
  const lessonsReady = graph.data?.skills.filter((s) => s.lesson_status === "ready").length ?? 0;
  const failedLessons = graph.data?.skills.filter((s) => s.lesson_status === "failed").length ?? 0;
  // While building, the mapped skills and newly written lessons change the menu (and its "nothing to do" message);
  // afterwards it only changes through actions.
  const next = useResource(
    courseData ? `next:${courseId}:${building ? `${skillCount}:${lessonsReady}` : "ready"}` : null,
    (signal) => getNext(courseId, signal),
    { keepPrevious: true },
  );
  const { reload: reloadCourse } = course;
  const { reload: reloadGraph, mutate: mutateGraph } = graph;
  const { reload: reloadNext } = next;
  const onCooldown = useCallback(
    (skillId: string, message: string) => {
      setCooldowns((prev) => ({ ...prev, [skillId]: message }));
      // The map and the menu may be stale (the lesson was halted in another tab or before this page loaded).
      void reloadGraph();
      void reloadNext();
    },
    [reloadGraph, reloadNext],
  );
  const { continueNext, startSkill, pending, replaceDialog } = useStartTask(courseId, next.data?.task ?? null, onCooldown);
  useDocumentTitle(courseData ? plainText(prettyTitle(courseData.title)) : "Course");

  const refreshAll = useCallback(() => {
    void reloadCourse();
    void reloadGraph();
    void reloadNext();
  }, [reloadCourse, reloadGraph, reloadNext]);

  const onJobChange = useCallback(
    (status: JobStatus) => {
      setJobOverride(status);
      refreshAll();
    },
    [refreshAll],
  );

  const onFoundationChange = useCallback(
    (updated: FoundationNode) => {
      mutateGraph((prev) => prev && { ...prev, foundations: prev.foundations.map((f) => (f.key === updated.key ? updated : f)) });
    },
    [mutateGraph],
  );

  const onDelete = async () => {
    setDeleting(true);
    try {
      await deleteCourse(courseId);
      toast.show("Course deleted.", "success");
      navigate("/");
    } catch (err) {
      toast.error(err);
      setDeleting(false);
    }
  };

  if (course.error && !courseData) {
    return (
      <div className="page">
        <ErrorState
          error={course.error}
          title={isApiError(course.error, 404) ? "This course doesn't exist" : "Couldn't open this course"}
          onRetry={isApiError(course.error, 404) ? undefined : () => void course.reload()}
        />
      </div>
    );
  }
  if (!courseData) return <LoadingState label="Opening the course…" />;

  const currentView: View = view ?? (narrow ? "list" : "map");
  const showBuild =
    building || job?.state === "failed" || job?.state === "interrupted" || courseData.status === "failed" || failedLessons > 0;
  const options = next.data?.options ?? [];
  const graphData = graph.data;
  const hasSkills = Boolean(graphData && graphData.skills.length > 0);
  // Nothing to start yet while the build runs: say what is happening rather than a possibly stale server message
  // ("This course has no skills yet." from before the map existed).
  const idleMessage = building
    ? !hasSkills
      ? "The chapter is still being mapped into skills — lessons follow once that's done."
      : lessonsReady === 0
        ? "Lessons are being written — the first one appears here as soon as it's ready."
        : next.data?.message || "Lessons are still being generated…"
    : next.data?.message || "Nothing to do right now.";

  return (
    <CourseProvider courseId={courseId} pageRange={[courseData.start_page, courseData.end_page]}>
      <div className="page course-page">
        <CourseHeader
          course={courseData}
          next={next.data}
          idleMessage={idleMessage}
          onContinue={() => void continueNext()}
          continuing={pending === "continue"}
          onEditGoal={() => setGoalOpen(true)}
        />

        {showBuild && <BuildProgress courseId={courseId} initialJob={job} onChange={onJobChange} failedLessons={building ? 0 : failedLessons} />}

        {next.data?.session.goal_reached && (
          <div className="callout callout--success course-page__goal">
            <Icon name="check" size={18} />
            <span>
              <strong>Daily goal reached.</strong> This is a good place to stop — anything more today is a bonus.
            </span>
          </div>
        )}

        <section className="course-page__section" aria-labelledby="next-heading">
          <div className="section-title">
            <h2 id="next-heading">Up next</h2>
          </div>
          {options.length > 0 ? (
            <TaskMenu
              options={options}
              pendingKey={pending}
              onStart={(option: TaskOption) => void startSkill(option.skill.id, option.type)}
            />
          ) : next.error && !next.data ? (
            <ErrorState error={next.error} onRetry={() => void next.reload()} title="Couldn't load your next tasks" />
          ) : next.data ? (
            <div className="course-page__empty-menu">
              <Icon name={building ? "clock" : "check"} size={18} />
              <span>{idleMessage}</span>
            </div>
          ) : (
            <div className="skeleton course-page__menu-skeleton" />
          )}
        </section>

        <section className="course-page__section" aria-labelledby="map-heading">
          <div className="course-page__map-head">
            <h2 id="map-heading">Knowledge map</h2>
            {graphData && building && hasSkills && (
              <span className="course-page__lessons muted">
                <Spinner size={12} label="Writing lessons" /> {lessonsReady}/{graphData.skills.length} lessons ready
              </span>
            )}
            <div className="segmented" role="group" aria-label="Map view">
              <button type="button" aria-pressed={currentView === "map"} onClick={() => setView("map")}>
                <Icon name="graph" size={15} /> Map
              </button>
              <button type="button" aria-pressed={currentView === "list"} onClick={() => setView("list")}>
                <Icon name="list" size={15} /> List
              </button>
            </div>
          </div>

          {!graphData && !building ? (
            graph.error ? (
              <ErrorState error={graph.error} onRetry={() => void graph.reload()} title="Couldn't load the knowledge map" />
            ) : (
              <div className="skeleton course-page__map-skeleton" />
            )
          ) : !graphData || !hasSkills ? (
            <StateMessage icon="graph" title={building ? "The map is on its way" : "No skills in this course"}>
              {building
                ? "Pages are being read and the chapter mapped into skills. The map appears here as soon as that's done — usually a few minutes."
                : "The build finished without creating any skills. Try resuming the build or creating the course again."}
            </StateMessage>
          ) : currentView === "map" ? (
            <>
              <SkillMap graph={graphData} selection={selection} onSelect={setSelection} />
              <ul className="map-legend" aria-label="Legend">
                {LEGEND.map((item) => (
                  <li key={item.status}>
                    <span className="map-legend__swatch" data-status={item.status} aria-hidden="true" />
                    {item.label}
                  </li>
                ))}
                <li className="map-legend__note">Arrows point from a skill to what it unlocks · thicker = practised more</li>
              </ul>
            </>
          ) : (
            <SkillList graph={graphData} selection={selection} onSelect={setSelection} />
          )}
        </section>

        {courseData.excluded.length > 0 && (
          <details className="disclosure course-page__excluded">
            <summary>Left out of this course ({courseData.excluded.length})</summary>
            <div className="disclosure__body">
              <ul>
                {courseData.excluded.map((item, i) => (
                  <li key={i}>
                    <Markdown inline>{item}</Markdown>
                  </li>
                ))}
              </ul>
            </div>
          </details>
        )}

        <footer className="course-page__footer">
          <button type="button" className="btn btn--ghost btn--sm btn--danger" onClick={() => setConfirmDelete(true)}>
            <Icon name="trash" size={14} /> Delete this course
          </button>
        </footer>
      </div>

      {graphData && (
        <SkillDrawer
          courseId={courseId}
          graph={graphData}
          selection={selection}
          onSelect={setSelection}
          onClose={() => setSelection(null)}
          // "Learn anyway" forces a locked skill; resuming one started that way needs no force (a 409 "locked" from an
          // older server is retried once with force by api.startTask). "Start anyway" forces a lesson that is cooling down.
          onLearn={(skill, options) => void startSkill(skill.id, "lesson", { force: Boolean(options?.force) || skill.status === "locked" })}
          onReview={(skill) => void startSkill(skill.id, "review")}
          onPreview={(skill) => setPreviewSkill(skill)}
          onFoundationChange={onFoundationChange}
          pending={pending}
          activeTask={next.data?.task ?? null}
          cooldownMessages={cooldowns}
        />
      )}
      <LessonPreviewDialog skill={previewSkill} onClose={() => setPreviewSkill(null)} />
      {replaceDialog}
      <GoalDialog
        open={goalOpen}
        current={next.data?.session.daily_goal ?? courseData.stats.daily_goal}
        onClose={() => setGoalOpen(false)}
        onSaved={() => {
          toast.show("Daily goal updated.", "success");
          refreshAll();
        }}
      />
      <Dialog
        open={confirmDelete}
        onClose={() => setConfirmDelete(false)}
        title="Delete this course?"
        size="sm"
        footer={
          <>
            <button type="button" className="btn btn--ghost" onClick={() => setConfirmDelete(false)}>
              Keep it
            </button>
            <button type="button" className="btn btn--danger-solid" onClick={() => void onDelete()} disabled={deleting}>
              {deleting && <Spinner size={14} label="Deleting" />} Delete course
            </button>
          </>
        }
      >
        <p>
          “{prettyTitle(courseData.title)}”, its lessons and your progress and review history will be removed. The book itself stays in your
          library. This can't be undone.
        </p>
      </Dialog>
    </CourseProvider>
  );
}

