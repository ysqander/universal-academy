import { lazy, Suspense } from "react";
import { createBrowserRouter, Outlet, RouterProvider, ScrollRestoration, useParams } from "react-router-dom";
import { AppHeader } from "./components/AppHeader";
import { RouteError } from "./components/RouteError";
import { LoadingState, StateMessage } from "./components/StateMessage";
import { Toaster } from "./components/Toaster";
import { ChapterPickerPage } from "./pages/picker/ChapterPickerPage";
import { LibraryPage } from "./pages/library/LibraryPage";

const CoursePage = lazy(() => import("./pages/course/CoursePage"));
const StudyPage = lazy(() => import("./pages/study/StudyPage"));

function ShellLayout() {
  return (
    <>
      <ScrollRestoration />
      <AppHeader />
      <main id="main" className="app-main">
        <Suspense fallback={<LoadingState />}>
          <Outlet />
        </Suspense>
      </main>
    </>
  );
}

function CourseRoute() {
  const { courseId = "" } = useParams();
  return <CoursePage key={courseId} courseId={courseId} />;
}

function StudyRoute() {
  const { courseId = "" } = useParams();
  return (
    <Suspense fallback={<LoadingState label="Preparing your next task…" />}>
      <ScrollRestoration />
      <StudyPage key={courseId} courseId={courseId} />
    </Suspense>
  );
}

function NotFound() {
  return (
    <div className="page">
      <StateMessage icon="book" title="This page doesn't exist" actions={<a className="btn" href="/">Back to the library</a>}>
        The link may be out of date.
      </StateMessage>
    </div>
  );
}

const router = createBrowserRouter([
  {
    path: "/",
    element: <ShellLayout />,
    errorElement: <RouteError />,
    children: [
      { index: true, element: <LibraryPage /> },
      { path: "books/:bookId", element: <ChapterPickerPage /> },
      { path: "courses/:courseId", element: <CourseRoute /> },
      { path: "*", element: <NotFound /> },
    ],
  },
  { path: "/courses/:courseId/study", element: <StudyRoute />, errorElement: <RouteError /> },
]);

export function App() {
  return (
    <Toaster>
      <RouterProvider router={router} />
    </Toaster>
  );
}
