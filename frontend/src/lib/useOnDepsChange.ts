import { useState } from "react";

function sameDeps(
  previous: readonly unknown[],
  next: readonly unknown[],
): boolean {
  return (
    previous.length === next.length &&
    previous.every((value, index) => Object.is(value, next[index]))
  );
}

/**
 * Calls `onChange` during the render in which `deps` differ from the previous
 * render's. It never fires on the first render.
 *
 * This is the adjust-state-while-rendering pattern from the React docs, for
 * state that follows a prop: a modal that clears its form each time it opens,
 * a draft that tracks its saved value. Done in an effect, the reset commits a
 * frame after the change and React paints the stale value in between; done
 * here, React restarts the render with the new state before anything commits.
 *
 * `onChange` may only set state that belongs to the calling component. Setting
 * another component's state during render is an error.
 */
export function useOnDepsChange(
  deps: readonly unknown[],
  onChange: () => void,
): void {
  const [previous, setPrevious] = useState(deps);
  if (!sameDeps(previous, deps)) {
    setPrevious(deps);
    onChange();
  }
}
