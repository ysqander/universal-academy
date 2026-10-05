import { isRouteErrorResponse, useRouteError } from "react-router-dom";
import { StateMessage } from "./StateMessage";

/** Last-resort error screen for a route that threw while rendering. */
export function RouteError() {
  const error = useRouteError();
  const notFound = isRouteErrorResponse(error) && error.status === 404;
  const detail = isRouteErrorResponse(error) ? error.statusText : error instanceof Error ? error.message : null;
  return (
    <div className="page">
      <StateMessage
        icon="warning"
        tone="danger"
        title={notFound ? "This page doesn't exist" : "Something went wrong on this screen"}
        actions={
          <>
            <button type="button" className="btn btn--primary" onClick={() => window.location.reload()}>
              Reload
            </button>
            <a className="btn" href="/">
              Back to the library
            </a>
          </>
        }
      >
        {detail ?? "An unexpected error occurred."}
      </StateMessage>
    </div>
  );
}
