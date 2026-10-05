import { useEffect } from "react";

const APP = "Universal Academy";

export function useDocumentTitle(title: string | null | undefined): void {
  useEffect(() => {
    document.title = title ? `${title} · ${APP}` : APP;
  }, [title]);
}
