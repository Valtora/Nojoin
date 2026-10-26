/**
 * Shared by the prepaint script and the hydrated application. Keep this function
 * self-contained plain JavaScript: theme-script.ts embeds its source verbatim.
 *
 * @param {string} style
 * @param {HTMLElement} root
 * @param {boolean} prepaint Whether the initial navigation still needs a paint guard.
 */
export function applyUiStyleToRoot(style, root, prepaint = false) {
  const document = root.ownerDocument;
  const id = "nojoin-optional-style";
  const optional = style === "classic" || style === "depth";
  if (prepaint || !optional) root.setAttribute("data-style", optional ? style : "default");
  let link = document.getElementById(id);

  if (link && !optional) {
    link.dispatchEvent(new Event("nojoin-style-cancel"));
    link.remove();
    link = null;
  }
  if (!optional) return;
  if (link) {
    // One shared download, but only the latest choice may be applied on load.
    link.dataset.uiStyleTarget = style;
    if (link.dataset.uiStyleState === "ready") root.setAttribute("data-style", style);
    return;
  }

  const stylesheet = document.createElement("link");
  stylesheet.id = id;
  stylesheet.rel = "stylesheet";
  stylesheet.href = "/styles/optional.css";
  if (prepaint) stylesheet.setAttribute("blocking", "render");
  stylesheet.dataset.uiStyleState = "loading";
  stylesheet.dataset.uiStyleTarget = style;

  // Ancestor compositing hides even descendants with visibility:visible.
  // Preserve priority as well as value, including an already transparent root.
  const opacity = root.style.getPropertyValue("opacity");
  const priority = root.style.getPropertyPriority("opacity");
  if (prepaint) root.style.setProperty("opacity", "0", "important");

  function finish(state) {
    if (document.getElementById(id) !== stylesheet || stylesheet.dataset.uiStyleState !== "loading") return;
    stylesheet.dataset.uiStyleState = state;
    window.clearTimeout(timeout);
    // Guard ownership stays with the creating call, including hydration reuse.
    if (prepaint) {
      if (opacity) root.style.setProperty("opacity", opacity, priority);
      else root.style.removeProperty("opacity");
    }
    if (state === "ready") root.setAttribute("data-style", stylesheet.dataset.uiStyleTarget);
    // Removing a stalled link also releases native blocking="render"; merely
    // restoring opacity would leave supporting browsers unable to paint.
    if (state === "failed") {
      stylesheet.remove();
      console.warn("Unable to load the optional UI stylesheet; keeping the current style.");
    }
  }

  // A stalled request must not leave the page invisible indefinitely. The
  // preference remains selected on failure, and a later application can retry.
  const timeout = window.setTimeout(() => finish("failed"), 10000);
  stylesheet.addEventListener("load", () => finish("ready"), { once: true });
  stylesheet.addEventListener("error", () => finish("failed"), { once: true });
  stylesheet.addEventListener("nojoin-style-cancel", () => finish("cancelled"), { once: true });
  document.head.appendChild(stylesheet);
}
