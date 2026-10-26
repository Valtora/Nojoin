/**
 * Shared by the prepaint script and the hydrated application. Keep this function
 * self-contained plain JavaScript: theme-script.ts embeds its source verbatim.
 *
 * @param {string} style
 * @param {HTMLElement} root
 * @param {boolean} prepaint Whether the initial navigation still needs a paint guard.
 */
export function applyUiStyleToRoot(style, root, prepaint = false) {
  // Allow cached/fast CSS to avoid a Default flash without delaying usable
  // content for more than a quarter of a second on a slow connection.
  const PREPAINT_GRACE_MS = 250;
  const document = root.ownerDocument;
  const id = "nojoin-optional-style";
  const optional = style === "classic" || style === "depth";
  if (!optional) root.setAttribute("data-style", "default");
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
  if (prepaint) root.setAttribute("data-style", style);

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
  let guarded = prepaint;

  function reveal() {
    if (!guarded) return;
    guarded = false;
    // Release native render blocking too, while retaining the pending download.
    stylesheet.removeAttribute("blocking");
    if (opacity) root.style.setProperty("opacity", opacity, priority);
    else root.style.removeProperty("opacity");
  }

  function finish(state) {
    if (document.getElementById(id) !== stylesheet || stylesheet.dataset.uiStyleState !== "loading") return;
    stylesheet.dataset.uiStyleState = state;
    window.clearTimeout(timeout);
    if (state === "ready") root.setAttribute("data-style", stylesheet.dataset.uiStyleTarget);
    else if (prepaint) root.setAttribute("data-style", "default");
    // Guard ownership stays with the creating call, including hydration reuse.
    reveal();
    if (state === "failed") {
      stylesheet.remove();
      console.warn("Unable to load the optional UI stylesheet; keeping the current style.");
    }
  }

  // Initial navigation falls back visibly but still honours a late stylesheet.
  // Runtime switches retain the current look and can retry a timed-out request.
  const timeout = window.setTimeout(() => {
    if (prepaint) {
      root.setAttribute("data-style", "default");
      reveal();
    } else finish("failed");
  }, prepaint ? PREPAINT_GRACE_MS : 10000);
  stylesheet.addEventListener("load", () => finish("ready"), { once: true });
  stylesheet.addEventListener("error", () => finish("failed"), { once: true });
  stylesheet.addEventListener("nojoin-style-cancel", () => finish("cancelled"), { once: true });
  document.head.appendChild(stylesheet);
}
