import { useState } from "react";
import { Spinner } from "./Spinner";
import "./PageImage.css";

interface PageImageProps {
  src: string;
  alt: string;
}

/** A rendered book page with a loading placeholder (keeps its box so the dialog doesn't jump). */
export function PageImage({ src, alt }: PageImageProps) {
  const [state, setState] = useState<{ src: string; status: "loading" | "ready" | "error" }>({ src, status: "loading" });
  const status = state.src === src ? state.status : "loading";
  return (
    <div className="page-image" data-status={status}>
      {status === "loading" && (
        <div className="page-image__placeholder">
          <Spinner size={22} label="Loading page" />
        </div>
      )}
      {status === "error" ? (
        <div className="page-image__placeholder">This page image couldn't be loaded.</div>
      ) : (
        <img
          key={src}
          src={src}
          alt={alt}
          onLoad={() => setState({ src, status: "ready" })}
          onError={() => setState({ src, status: "error" })}
        />
      )}
    </div>
  );
}
