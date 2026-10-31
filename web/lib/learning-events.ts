// Client-side signal that the learner's *content* changed.
//
// The sidebar's learning list (imported files, mastery topics, books) is
// fetched once per shell mount, but an import or a review happens inside a
// page that knows nothing about the sidebar. Without a signal the new file
// only appears after a manual refresh, which reads as "the import failed".
// Same shape as `session-events`, for the same reason.

const EVENT_NAME = "learning:changed";

type Listener = () => void;

function getTarget(): EventTarget | null {
  if (typeof window === "undefined") return null;
  return window;
}

/** Tell every mounted learning list to refetch. */
export function notifyLearningChanged(): void {
  getTarget()?.dispatchEvent(new Event(EVENT_NAME));
}

export function subscribeLearningChanges(listener: Listener): () => void {
  const target = getTarget();
  if (!target) return () => {};
  target.addEventListener(EVENT_NAME, listener);
  return () => target.removeEventListener(EVENT_NAME, listener);
}
