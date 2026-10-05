// Typed client for the FastAPI backend (docs/SPEC.md §5). Every endpoint has one function here.
import type {
  AnswerResult,
  AnswerSubmit,
  Book,
  BookSummary,
  Course,
  CourseCreate,
  CourseGraph,
  CourseSummary,
  FoundationNode,
  Health,
  JobStatus,
  Lesson,
  NextTaskResponse,
  RevealResponse,
  SourcePage,
  StartTaskRequest,
  Task,
} from "./types";

// Request bodies / small payloads that are not part of the shared types.ts mirror.
export interface Settings {
  daily_goal: number;
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

export const isAbortError = (err: unknown): boolean => err instanceof DOMException && err.name === "AbortError";

const UNREACHABLE = "Can't reach the Universal Academy server. Is the backend running on port 8000?";

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
  if (status >= 500) return status === 502 || status === 503 || status === 504 ? UNREACHABLE : `Server error (${status}).`;
  return `Request failed (${status}).`;
}

function errorFromBody(status: number, bodyText: string): ApiError {
  let message: string | null = null;
  try {
    message = detailToMessage((JSON.parse(bodyText) as { detail?: unknown }).detail);
  } catch {
    // Non-JSON error bodies (e.g. a proxy error page) fall through to a status-based message.
  }
  if (!message && status === 500 && !bodyText.trim()) message = UNREACHABLE; // Vite proxy: backend down
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
    throw new ApiError(0, UNREACHABLE);
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

// ---------- Books ----------
export const listBooks = (signal?: AbortSignal) => get<BookSummary[]>("/api/books", signal);
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
    xhr.onerror = () => reject(new ApiError(0, UNREACHABLE));
    xhr.onabort = () => reject(new DOMException("Upload cancelled", "AbortError"));
    const form = new FormData();
    form.append("file", file, file.name);
    xhr.send(form);
  });
  return { promise, abort: () => xhr.abort() };
}

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
export const setFoundationKnown = (courseId: string, key: string, known: boolean) =>
  post<FoundationNode>(`/api/foundations/${seg(courseId)}/${seg(key)}`, { known });

// ---------- Study loop ----------
export const getNext = (courseId: string, signal?: AbortSignal) =>
  get<NextTaskResponse>(`/api/courses/${seg(courseId)}/next`, signal);
export const continueLearning = (courseId: string) => post<Task>(`/api/courses/${seg(courseId)}/continue`);
export const startTask = (courseId: string, body: StartTaskRequest) => post<Task>(`/api/courses/${seg(courseId)}/tasks`, body);
export const getTask = (taskId: string, signal?: AbortSignal) => get<Task>(`/api/tasks/${seg(taskId)}`, signal);
export const startPractice = (taskId: string) => post<Task>(`/api/tasks/${seg(taskId)}/practice`);
export const submitAnswer = (taskId: string, body: AnswerSubmit) => post<AnswerResult>(`/api/tasks/${seg(taskId)}/answer`, body);
export const easierStep = (taskId: string) => post<Task>(`/api/tasks/${seg(taskId)}/easier`);
export const abandonTask = (taskId: string) => post<Task>(`/api/tasks/${seg(taskId)}/abandon`);
export const revealSolution = (questionId: string) => post<RevealResponse>(`/api/questions/${seg(questionId)}/reveal`);
export const flagQuestion = (questionId: string, reason: string) =>
  post<Task>(`/api/questions/${seg(questionId)}/flag`, { reason });

export function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error && err.message) return err.message;
  return "Something went wrong.";
}
