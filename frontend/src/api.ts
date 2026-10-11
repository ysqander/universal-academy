// Typed client for the FastAPI backend (docs/SPEC.md §5). Every endpoint has one function here.
import { isCooldownMessage } from "./lib/cooldown";
import { isQuizFirstMessage } from "./lib/quiz";
import type {
  AnswerResult,
  AnswerSubmit,
  ApiKeys,
  ApiKeysUpdate,
  BlockCheckRequest,
  BlockCheckResult,
  BlockFeedbackRequest,
  Book,
  BookSummary,
  Course,
  CourseCreate,
  CourseGraph,
  CourseSummary,
  ExampleCourse,
  ExampleImportResult,
  FoundationNode,
  GuidedCheckResult,
  GuidedRevealRequest,
  Health,
  HintResponse,
  JobStatus,
  Lesson,
  NextTaskResponse,
  RevealRequest,
  RevealResponse,
  SourcePage,
  StartTaskRequest,
  Task,
} from "./types";

// Request bodies / small payloads that are not part of the shared types.ts mirror.
export interface Settings {
  daily_goal: number;
  /** Quiz time accommodation (SPEC §6.7): 1 normal, 1.5 (+50 %), 2 (double). Older servers: absent (= 1). */
  quiz_time_factor?: number;
}

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export const isApiError = (err: unknown, status?: number): err is ApiError =>
  err instanceof ApiError && (status === undefined || err.status === status);

/** 409 from starting a lesson during its cooldown ("This lesson comes back at 14:43 (local time) …"): not an error —
 * say it as information; `force: true` starts it anyway. */
export const isCooldownConflict = (err: unknown): err is ApiError => isApiError(err, 409) && isCooldownMessage(err.message);

export const isAbortError = (err: unknown): boolean => err instanceof DOMException && err.name === "AbortError";

/** No answer at all: the network request failed, or the dev proxy found no backend behind it. No port is assumed —
 * the app is served by the same server it talks to, on whatever port that runs. */
const unreachable = (): string => {
  const host = typeof window !== "undefined" ? window.location.host : "";
  return `Can't reach the Universal Academy server${host ? ` at ${host}` : ""}. Check that it is still running, then try again.`;
};

type ValidationIssue = { loc?: (string | number)[]; msg?: string };

function detailToMessage(detail: unknown): string | null {
  if (typeof detail === "string" && detail.trim()) return detail;
  if (Array.isArray(detail)) {
    const parts = (detail as ValidationIssue[])
      .map((issue) => {
        const field = issue.loc?.filter((p) => p !== "body").join(".");
        return issue.msg ? (field ? `${field}: ${issue.msg}` : issue.msg) : null;
      })
      .filter((p): p is string => Boolean(p));
    if (parts.length) return parts.join("; ");
  }
  return null;
}

function fallbackMessage(status: number): string {
  if (status === 404) return "Not found.";
  if (status === 409) return "That isn't possible right now.";
  if (status === 413) return "The file is too large.";
  if (status === 422) return "The request was rejected as invalid.";
  // A real 502/503/504 response came from a server that is reachable: it is busy or failing, not gone.
  if (status === 502 || status === 503 || status === 504) return `The server couldn't handle this right now (${status}). Try again in a moment.`;
  if (status >= 500) return `Server error (${status}).`;
  return `Request failed (${status}).`;
}

function errorFromBody(status: number, bodyText: string): ApiError {
  let message: string | null = null;
  try {
    message = detailToMessage((JSON.parse(bodyText) as { detail?: unknown }).detail);
  } catch {
    // Non-JSON error bodies (e.g. a proxy error page) fall through to a status-based message.
  }
  // Vite dev proxy with no backend behind it (location.host is then the dev server, so it isn't named).
  if (!message && status === 500 && !bodyText.trim()) {
    message = "Can't reach the Universal Academy backend behind the dev server. Check that it is running (and UA_API_TARGET, if set).";
  }
  return new ApiError(status, message ?? fallbackMessage(status));
}

async function request<T>(method: string, path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      method,
      signal,
      headers: body === undefined ? { Accept: "application/json" } : { Accept: "application/json", "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (err) {
    if (isAbortError(err)) throw err;
    throw new ApiError(0, unreachable());
  }
  const text = await response.text();
  if (!response.ok) throw errorFromBody(response.status, text);
  if (!text) return undefined as T;
  try {
    return JSON.parse(text) as T;
  } catch {
    throw new ApiError(response.status, "The server sent an unreadable response.");
  }
}

const get = <T>(path: string, signal?: AbortSignal) => request<T>("GET", path, undefined, signal);
const post = <T>(path: string, body?: unknown) => request<T>("POST", path, body ?? {});
const put = <T>(path: string, body: unknown) => request<T>("PUT", path, body);
const del = (path: string) => request<void>("DELETE", path);
const seg = encodeURIComponent;

// ---------- Health & settings ----------
export const getHealth = (signal?: AbortSignal) => get<Health>("/api/health", signal);
export const getSettings = (signal?: AbortSignal) => get<Settings>("/api/settings", signal);
export const putSettings = (settings: Settings) => put<Settings>("/api/settings", settings);
/** API keys saved in the app (never the keys themselves: set or not, last 4 characters, where they come from). */
export const getKeys = (signal?: AbortSignal) => get<ApiKeys>("/api/settings/keys", signal);
/** Save keys and/or the provider; an empty string removes a saved key. 403 when not on the computer running the app. */
export const saveKeys = (body: ApiKeysUpdate) => put<ApiKeys>("/api/settings/keys", body);

// ---------- Books ----------
export const listBooks = (signal?: AbortSignal) => get<BookSummary[]>("/api/books", signal);
export const deleteBook = (bookId: string) => del(`/api/books/${seg(bookId)}`);
export const getBook = (bookId: string, signal?: AbortSignal) => get<Book>(`/api/books/${seg(bookId)}`, signal);
export const importBookFromPath = (path: string) => post<Book>("/api/books/import-path", { path });
export const bookPageUrl = (bookId: string, page: number, dpi = 60) =>
  `/api/books/${seg(bookId)}/pages/${page}.png?dpi=${dpi}`;

export interface Upload<T> {
  promise: Promise<T>;
  abort: () => void;
}

/** Multipart upload via XHR so the UI can show byte progress (fetch has no upload progress). */
export function uploadBook(file: File, onProgress: (fraction: number) => void): Upload<Book> {
  const xhr = new XMLHttpRequest();
  const promise = new Promise<Book>((resolve, reject) => {
    xhr.open("POST", "/api/books");
    xhr.setRequestHeader("Accept", "application/json");
    xhr.responseType = "text";
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(event.loaded / event.total);
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(JSON.parse(xhr.responseText) as Book);
        } catch {
          reject(new ApiError(xhr.status, "The server sent an unreadable response."));
        }
      } else {
        reject(errorFromBody(xhr.status, xhr.responseText));
      }
    };
    xhr.onerror = () => reject(new ApiError(0, unreachable()));
    xhr.onabort = () => reject(new DOMException("Upload cancelled", "AbortError"));
    const form = new FormData();
    form.append("file", file, file.name);
    xhr.send(form);
  });
  return { promise, abort: () => xhr.abort() };
}

// ---------- Example courses (repo examples/: studied with no API key) ----------
export const listExamples = (signal?: AbortSignal) => get<ExampleCourse[]>("/api/examples", signal);
export const importExample = (name: string) => post<ExampleImportResult>(`/api/examples/${seg(name)}/import`);

// ---------- Courses & build job ----------
export const listCourses = (signal?: AbortSignal) => get<CourseSummary[]>("/api/courses", signal);
export const createCourse = (body: CourseCreate) => post<Course>("/api/courses", body);
export const getCourse = (courseId: string, signal?: AbortSignal) => get<Course>(`/api/courses/${seg(courseId)}`, signal);
export const getJob = (courseId: string, signal?: AbortSignal) =>
  get<JobStatus>(`/api/courses/${seg(courseId)}/job`, signal);
export const resumeBuild = (courseId: string) => post<JobStatus>(`/api/courses/${seg(courseId)}/resume`);
export const deleteCourse = (courseId: string) => del(`/api/courses/${seg(courseId)}`);
export const getGraph = (courseId: string, signal?: AbortSignal) =>
  get<CourseGraph>(`/api/courses/${seg(courseId)}/graph`, signal);
export const figureUrl = (courseId: string, figureId: string) =>
  `/api/courses/${seg(courseId)}/figures/${seg(figureId)}.png`;
export const getSourcePage = (courseId: string, page: number, signal?: AbortSignal) =>
  get<SourcePage>(`/api/courses/${seg(courseId)}/source/${page}`, signal);
export const getLesson = (skillId: string, signal?: AbortSignal) => get<Lesson>(`/api/skills/${seg(skillId)}/lesson`, signal);
/** Answer a v2 lesson's inline check (formative: graded, never recorded). `reflect` checks send an empty body. */
export const checkBlock = (skillId: string, blockIndex: number, body: BlockCheckRequest) =>
  post<BlockCheckResult>(`/api/skills/${seg(skillId)}/blocks/${blockIndex}/check`, body);
/**
 * Answer a scored step of a proof lesson inside its task (SPEC §6.3b; `version` = Lesson.version, required). 422 for
 * an unreadable or unsimplified answer (not a try); 409 for a stale lesson, an earlier step still open, or a finished
 * task. The result carries the step's record and the updated task (with the next blocks once the step is done).
 */
export const checkGuided = (taskId: string, blockIndex: number, body: BlockCheckRequest) =>
  post<GuidedCheckResult>(`/api/tasks/${seg(taskId)}/blocks/${blockIndex}/check`, body);
/** "Show answer" on a scored step: it is done with 0 points. */
export const revealGuided = (taskId: string, blockIndex: number, version: string) =>
  post<GuidedCheckResult>(`/api/tasks/${seg(taskId)}/blocks/${blockIndex}/reveal`, { version } satisfies GuidedRevealRequest);
/** Report a done scored step's answer key as wrong: kept for review (state.reported); the score doesn't change. */
export const flagGuided = (taskId: string, blockIndex: number, reason: string) =>
  post<Task>(`/api/tasks/${seg(taskId)}/blocks/${blockIndex}/flag`, { reason });
/** "This step isn't clear" on one teaching block (204). */
export const sendBlockFeedback = (skillId: string, blockIndex: number, body: BlockFeedbackRequest) =>
  post<void>(`/api/skills/${seg(skillId)}/blocks/${blockIndex}/feedback`, body);
export const setFoundationKnown = (courseId: string, key: string, known: boolean) =>
  post<FoundationNode>(`/api/foundations/${seg(courseId)}/${seg(key)}`, { known });

// ---------- Study loop ----------
export const getNext = (courseId: string, signal?: AbortSignal) =>
  get<NextTaskResponse>(`/api/courses/${seg(courseId)}/next`, signal);
export const continueLearning = (courseId: string) => post<Task>(`/api/courses/${seg(courseId)}/continue`);
// selector.start_task: 409 "S3 is locked — learn S1 (…) first." for a lesson on a skill whose prerequisites are open.
const LOCKED_CONFLICT = /\bis locked\b/;

/**
 * Start (or resume) a chosen task. A lesson the learner picked explicitly (drawer, menu) is theirs to take: when the
 * server still answers 409 "locked" — e.g. resuming a lesson started earlier with "Learn anyway" — retry once with
 * `force` (a no-op for unlocked skills), so the button never silently fails. A 409 cooldown ("comes back at …") is
 * never retried: only the learner's own "Start anyway" sends `force` for it.
 */
export async function startTask(courseId: string, body: StartTaskRequest): Promise<Task> {
  const path = `/api/courses/${seg(courseId)}/tasks`;
  try {
    return await post<Task>(path, body);
  } catch (err) {
    if (body.force || body.type === "review" || !(isApiError(err, 409) && LOCKED_CONFLICT.test(err.message))) throw err;
    return post<Task>(path, { ...body, force: true });
  }
}
/** selector.start_task's 409 while a quiz is due ("Take the quiz first — about 9 minutes."): information, not an error. */
export const isQuizFirstConflict = (err: unknown): err is ApiError => isApiError(err, 409) && isQuizFirstMessage(err.message);

/**
 * Start the quiz on offer (SPEC §6.7): POST /tasks with type "quiz" (a quiz has no skill; skill_id is ignored) starts
 * the due quiz, else the optional retake; an open quiz is resumed. `replaceActive` sets an open lesson/review aside.
 */
export const startQuiz = (courseId: string, replaceActive = false) =>
  post<Task>(`/api/courses/${seg(courseId)}/tasks`, { skill_id: "", type: "quiz", replace_active: replaceActive } satisfies StartTaskRequest);

export const getTask = (taskId: string, signal?: AbortSignal) => get<Task>(`/api/tasks/${seg(taskId)}`, signal);
export const startPractice = (taskId: string) => post<Task>(`/api/tasks/${seg(taskId)}/practice`);
export const submitAnswer = (taskId: string, body: AnswerSubmit) => post<AnswerResult>(`/api/tasks/${seg(taskId)}/answer`, body);
export const easierStep = (taskId: string) => post<Task>(`/api/tasks/${seg(taskId)}/easier`);
export const abandonTask = (taskId: string) => post<Task>(`/api/tasks/${seg(taskId)}/abandon`);
/** The learner opens the lesson during a task (a review records it as a hint on the current question). */
export const lookAtLesson = (taskId: string) => post<Task>(`/api/tasks/${seg(taskId)}/lesson-look`);
/** Show the model solution. Free response: pass the committed answer so this counts as a comparison, not as help. */
export const revealSolution = (questionId: string, response: string | null = null) =>
  post<RevealResponse>(`/api/questions/${seg(questionId)}/reveal`, { response } satisfies RevealRequest);
/** The next hint for the current attempt; recorded server-side, so it still counts after a reload. A multistep
 * question has one hint per step (`step`): using it makes that step worth ½ point. */
export const getHint = (questionId: string, step?: number | null) =>
  post<HintResponse>(`/api/questions/${seg(questionId)}/hint${typeof step === "number" ? `?step=${step}` : ""}`);
export const flagQuestion = (questionId: string, reason: string) =>
  post<Task>(`/api/questions/${seg(questionId)}/flag`, { reason });

export function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error && err.message) return err.message;
  return "Something went wrong.";
}
