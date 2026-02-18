
import { Extension } from '@tiptap/core';
import { Plugin, PluginKey } from '@tiptap/pm/state';
import { Decoration, DecorationSet } from '@tiptap/pm/view';

export interface SearchMatch {
    startIndex: number;
    length: number;
}

export interface SearchStorage {
    matches: SearchMatch[];
    currentIndex: number;
}

declare module '@tiptap/core' {
    interface Commands<ReturnType> {
        search: {
            setSearchMatches: (matches: SearchMatch[], currentIndex: number) => ReturnType;
        }
    }
}

export const SearchExtension = Extension.create<any, SearchStorage>({
    name: 'search',

    addStorage() {
        return {
            matches: [],
            currentIndex: -1,
        }
    },

    addCommands() {
        return {
            setSearchMatches: (matches: SearchMatch[], currentIndex: number) => ({ tr, dispatch }) => {
                if (dispatch) {
                    // Update storage
                    this.storage.matches = matches;
                    this.storage.currentIndex = currentIndex;

                    // Trigger update via metadata
                    tr.setMeta('search', { matches, currentIndex });
                }
                return true;
            },
        }
    },

    addProseMirrorPlugins() {
        return [
            new Plugin({
                key: new PluginKey('search'),
                state: {
                    init() {
                        return DecorationSet.empty;
                    },
                    apply: (tr, oldSet) => {
                        const meta = tr.getMeta('search');

                        // Rebuilds decorations when new match data is received from the command.
                        if (meta) {
                            const { matches, currentIndex } = meta as SearchStorage;
                            const decorations: Decoration[] = [];

                            matches.forEach((match, index) => {
                                const isCurrent = index === currentIndex;
                                const className = isCurrent
                                    ? 'bg-orange-400 text-white'
                                    : 'bg-yellow-200 dark:bg-yellow-900 text-gray-900 dark:text-gray-100';

                                decorations.push(
                                    Decoration.inline(match.startIndex, match.startIndex + match.length, {
                                        class: className
                                    })
                                );
                            });

                            return DecorationSet.create(tr.doc, decorations);
                        }

                        // Otherwise, map existing decorations to changes
                        return oldSet.map(tr.mapping, tr.doc);
                    },
                },
                props: {
                    decorations(state) {
                        return this.getState(state);
                    },
                },
            }),
        ];
    },
});
