import { useEffect, useId, useRef, useState, type DragEvent, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { errorMessage, importBookFromPath, isAbortError, uploadBook, type Upload } from "../../api";
import { Icon } from "../../components/Icon";
import { ProgressBar } from "../../components/ProgressBar";
import { Spinner } from "../../components/Spinner";
import { useToast } from "../../context/toast";
import type { Book } from "../../types";

type UploadState =
  | { phase: "idle" }
  | { phase: "uploading"; name: string; size: number; fraction: number }
  | { phase: "processing"; name: string; size: number };

const isPdf = (file: File): boolean => file.type === "application/pdf" || file.name.toLowerCase().endsWith(".pdf");

const formatSize = (bytes: number): string =>
  bytes >= 1e6 ? `${(bytes / 1e6).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1e3))} KB`;

export function ImportPanel() {
  const navigate = useNavigate();
  const toast = useToast();
  const inputId = useId();
  const pathId = useId();
  const fileInput = useRef<HTMLInputElement>(null);
  const upload = useRef<Upload<Book> | null>(null);
  const [state, setState] = useState<UploadState>({ phase: "idle" });
  const [dragging, setDragging] = useState(false);
  const [path, setPath] = useState("");
  const [pathBusy, setPathBusy] = useState(false);
  const [pathError, setPathError] = useState<string | null>(null);

  useEffect(() => () => upload.current?.abort(), []);

  const opened = (book: Book) => {
    toast.show(`Imported “${book.title}”. Choose a chapter to study.`, "success");
    navigate(`/books/${encodeURIComponent(book.id)}`);
  };

  const startUpload = async (file: File) => {
    if (!isPdf(file)) {
      toast.show(`“${file.name}” isn't a PDF. Choose a .pdf file.`, "error");
      return;
    }
    const { name, size } = file;
    setState({ phase: "uploading", name, size, fraction: 0 });
    const job = uploadBook(file, (fraction) =>
      setState(fraction >= 1 ? { phase: "processing", name, size } : { phase: "uploading", name, size, fraction }),
    );
    upload.current = job;
    try {
      opened(await job.promise);
    } catch (err) {
      if (!isAbortError(err)) toast.error(err);
      setState({ phase: "idle" });
    } finally {
      upload.current = null;
    }
  };

  const onDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setDragging(false);
    if (state.phase !== "idle") return;
    const file = event.dataTransfer.files[0];
    if (file) void startUpload(file);
  };

  const onDragOver = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = state.phase === "idle" ? "copy" : "none";
    if (!dragging) setDragging(true);
  };

  const onDragLeave = (event: DragEvent<HTMLDivElement>) => {
    if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDragging(false);
  };

  const submitPath = async (event: FormEvent) => {
    event.preventDefault();
    const trimmed = path.trim().replace(/^["']|["']$/g, "");
    if (!trimmed) {
      setPathError("Enter the full path to a PDF on this computer.");
      return;
    }
    setPathBusy(true);
    setPathError(null);
    try {
      opened(await importBookFromPath(trimmed));
    } catch (err) {
      setPathError(errorMessage(err));
    } finally {
      setPathBusy(false);
    }
  };

  const busy = state.phase !== "idle";

  return (
    <div className="import-panel">
      <div
        className="dropzone"
        data-dragging={dragging || undefined}
        data-busy={busy || undefined}
        onDragOver={onDragOver}
        onDragEnter={onDragOver}
        onDragLeave={onDragLeave}
        onDrop={onDrop}
      >
        {state.phase === "idle" ? (
          <>
            <span className="dropzone__icon">
              <Icon name="upload" size={24} />
            </span>
            <p className="dropzone__title">Drop a textbook PDF here</p>
            <p className="dropzone__hint">Text-based PDFs with a table of contents work best. Scanned books are best-effort.</p>
            <input
              ref={fileInput}
              id={inputId}
              type="file"
              accept="application/pdf,.pdf"
              className="visually-hidden"
              onChange={(event) => {
                const file = event.target.files?.[0];
                event.target.value = "";
                if (file) void startUpload(file);
              }}
            />
            <button type="button" className="btn btn--primary" onClick={() => fileInput.current?.click()}>
              <Icon name="folder" size={16} /> Choose a PDF…
            </button>
          </>
        ) : (
          <div className="dropzone__progress" aria-live="polite">
            <p className="dropzone__title">
              {state.name} <span className="muted">· {formatSize(state.size)}</span>
            </p>
            {state.phase === "uploading" ? (
              <>
                <ProgressBar value={Math.round(state.fraction * 100)} max={100} label="Upload progress" size="lg" />
                <p className="dropzone__hint">Uploading… {Math.round(state.fraction * 100)}%</p>
              </>
            ) : (
              <p className="dropzone__hint row">
                <Spinner size={14} label="Reading" /> Reading the outline and page labels…
              </p>
            )}
            <button type="button" className="btn btn--sm btn--ghost" onClick={() => upload.current?.abort()}>
              Cancel
            </button>
          </div>
        )}
      </div>

      <form className="import-path" onSubmit={submitPath} noValidate>
        <label className="field__label" htmlFor={pathId}>
          Or import from a path on this computer
        </label>
        <div className="import-path__row">
          <input
            id={pathId}
            className="input mono"
            type="text"
            value={path}
            onChange={(event) => {
              setPath(event.target.value);
              setPathError(null);
            }}
            placeholder="/Users/you/Books/linear-algebra.pdf"
            autoComplete="off"
            spellCheck={false}
            aria-invalid={pathError ? true : undefined}
            aria-describedby={pathError ? `${pathId}-error` : undefined}
            disabled={pathBusy || busy}
          />
          <button type="submit" className="btn" disabled={pathBusy || busy}>
            {pathBusy ? <Spinner size={14} label="Importing" /> : null}
            Import
          </button>
        </div>
        {pathError && (
          <p id={`${pathId}-error`} className="import-path__error" role="alert">
            {pathError}
          </p>
        )}
      </form>
      {state.phase === "idle" && <p className="import-panel__note muted">Files stay on this computer: books are copied into the app's local data folder.</p>}
    </div>
  );
}
