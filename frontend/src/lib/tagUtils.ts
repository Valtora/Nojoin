import { Tag } from '@/types';

/**
 * Builds a hierarchical path string for a tag, e.g., "Parent / Child".
 * @param tag The tag to build the path for.
 * @param allTags A list of all available tags to lookup parents.
 * @returns A string representing the full path of the tag.
 */
export function buildTagPath(tag: Tag, allTags: Tag[]): string {
    const path: string[] = [tag.name];
    let currentTag = tag;
    const visitedIds = new Set<number>();
    visitedIds.add(tag.id);

    while (currentTag.parent_id) {
        if (visitedIds.has(currentTag.parent_id)) {
            console.warn('Circular tag dependency detected for tag:', tag.id);
            break;
        }

        const parent = allTags.find(t => t.id === currentTag.parent_id);
        if (!parent) break;

        path.unshift(parent.name);
        currentTag = parent;
        visitedIds.add(currentTag.id);
    }

    return path.join(' -> ');
}
