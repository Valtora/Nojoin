import { Extension } from '@tiptap/core';
import { Plugin, PluginKey } from '@tiptap/pm/state';
import { Decoration, DecorationSet } from '@tiptap/pm/view';
import { spellCheckService } from './spellCheckService';

export interface SpellCheckError {
  from: number;
  to: number;
  word: string;
}

interface SpellCheckPluginState {
  decorations: DecorationSet;
  errors: SpellCheckError[];
}

const spellCheckPluginKey = new PluginKey('spellCheck');

const WORD_BOUNDARY_REGEX = /[\s.,;:!?"'()\[\]{}<>\/\\@#$%^&*+=|~`]+/;
const URL_REGEX = /^https?:\/\//i;
const EMAIL_REGEX = /\S+@\S+\.\S+/;

function shouldSkipWord(word: string): boolean {
  if (word.length < 2) return true;
  if (/^\d+$/.test(word)) return true;
  if (/[0-9]/.test(word)) return true;
  if (word === word.toUpperCase() && word.length > 1) return true;
  if (URL_REGEX.test(word)) return true;
  if (EMAIL_REGEX.test(word)) return true;
  // Skip markdown artefacts
  if (/^[#*_\-~>]+$/.test(word)) return true;
  return false;
}

function extractMisspelledWords(doc: any): SpellCheckError[] {
  const errors: SpellCheckError[] = [];
  if (!spellCheckService.isReady() || spellCheckService.isDisabled()) return errors;

  doc.descendants((node: any, pos: number) => {
    if (!node.isText) return;
    const text: string = node.text || '';

    let offset = 0;
    const tokens = text.split(WORD_BOUNDARY_REGEX);

    for (const token of tokens) {
      if (!token) {
        offset = text.indexOf(token, offset);
        if (offset === -1) break;
        offset += token.length;
        continue;
      }

      const idx = text.indexOf(token, offset);
      if (idx === -1) continue;

      // Strip leading/trailing punctuation that may not have been caught by the split
      const stripped = token.replace(/^[''""]+|[''""]+$/g, '');
      if (!stripped || shouldSkipWord(stripped)) {
        offset = idx + token.length;
        continue;
      }

      // Handle hyphenated words: check the compound first, then parts individually
      if (stripped.includes('-')) {
        if (!spellCheckService.check(stripped)) {
          const parts = stripped.split('-');
          let partOffset = 0;
          for (const part of parts) {
            const partStart = stripped.indexOf(part, partOffset);
            if (part && !shouldSkipWord(part) && !spellCheckService.check(part)) {
              const strippedStart = token.indexOf(stripped);
              errors.push({
                from: pos + idx + strippedStart + partStart,
                to: pos + idx + strippedStart + partStart + part.length,
                word: part,
              });
            }
            partOffset = partStart + part.length + 1;
          }
        }
      } else if (!spellCheckService.check(stripped)) {
        const strippedStart = token.indexOf(stripped);
        errors.push({
          from: pos + idx + strippedStart,
          to: pos + idx + strippedStart + stripped.length,
          word: stripped,
        });
      }

      offset = idx + token.length;
    }
  });

  return errors;
}

function buildDecorations(doc: any, errors: SpellCheckError[]): DecorationSet {
  const decorations = errors.map((err) =>
    Decoration.inline(err.from, err.to, {
      class: 'spellcheck-error',
      nodeName: 'span',
    })
  );
  return DecorationSet.create(doc, decorations);
}

/** Retrieves the spell check error at a given document position, if any. */
export function getSpellCheckErrorAtPos(
  view: any,
  pos: number
): SpellCheckError | null {
  const pluginState: SpellCheckPluginState | undefined =
    spellCheckPluginKey.getState(view.state);
  if (!pluginState) return null;

  return (
    pluginState.errors.find((err) => pos >= err.from && pos <= err.to) ?? null
  );
}

export const SpellCheckExtension = Extension.create({
  name: 'spellCheck',

  addProseMirrorPlugins() {
    let debounceTimer: ReturnType<typeof setTimeout> | null = null;
    const extensionThis = this;

    const plugin = new Plugin<SpellCheckPluginState>({
      key: spellCheckPluginKey,

      state: {
        init(_, state) {
          return {
            decorations: DecorationSet.empty,
            errors: [],
          };
        },
        apply(tr, pluginState, _oldState, newState) {
          const meta = tr.getMeta(spellCheckPluginKey);
          if (meta) {
            return meta as SpellCheckPluginState;
          }

          if (tr.docChanged) {
            return {
              decorations: pluginState.decorations.map(tr.mapping, tr.doc),
              errors: pluginState.errors.map((err) => {
                const from = tr.mapping.map(err.from, 1);
                const to = tr.mapping.map(err.to, -1);
                return from < to ? { ...err, from, to } : err;
              }).filter((err) => err.from < err.to),
            };
          }

          return pluginState;
        },
      },

      props: {
        decorations(state) {
          return this.getState(state)?.decorations ?? DecorationSet.empty;
        },
      },

      view(editorView) {
        const scheduleCheck = () => {
          if (debounceTimer) clearTimeout(debounceTimer);
          debounceTimer = setTimeout(() => {
            const { state } = editorView;
            const errors = extractMisspelledWords(state.doc);
            const decorations = buildDecorations(state.doc, errors);

            const tr = state.tr.setMeta(spellCheckPluginKey, {
              decorations,
              errors,
            } satisfies SpellCheckPluginState);
            editorView.dispatch(tr);
          }, 400);
        };

        // Re-check when the spell check service signals readiness (language change, dictionary update)
        const unsubscribe = spellCheckService.onReady(() => {
          scheduleCheck();
        });

        // Initial check
        scheduleCheck();

        return {
          update(view, prevState) {
            if (view.state.doc !== prevState.doc) {
              scheduleCheck();
            }
          },
          destroy() {
            if (debounceTimer) clearTimeout(debounceTimer);
            unsubscribe();
          },
        };
      },
    });

    return [plugin];
  },
});
