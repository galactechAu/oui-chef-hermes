# Meals and Recipe Books

Meals is the saved recipe library. Recipe Books organise references to those Meals; adding a Meal to several Books does not copy the recipe. Suggested and imported recipes still require review before being saved.

## Organise your library

1. Open **Recipe Books** and create a named Book.
2. Switch to **Uncategorised** to see saved Meals with no Book memberships.
3. Choose **Select meals**, select the Meals you want, then choose **Add to Recipe Books**.
4. Select one or more Books. You can create another Book inside the picker without losing the Meal selection.
5. Review the Meal and Book counts and save.

The same selection workflow is available from Meals. No dragging is required; selection controls and the picker work with a keyboard and on touchscreens.

## Browse and maintain Books

- Cards show a cover from an available Meal image, or a neutral fallback, and the Meal count.
- Pinned Books appear first, followed by recently updated Books.
- Search matches Book titles and contained Meal titles. Matching Meal names appear in results.
- More results load as you scroll. Opening a Book shows its contained Meals.
- Use **Manage Recipe Book** to rename or delete it. Deleting a Book removes its memberships only: Meals stay in your library and shopping-list history is unchanged.

## Meal actions

Open a Meal card to use its action panel. The panel includes the recipe link, a five-star rating, shopping-list actions, membership management, and the relevant removal action. Recently used Books provide quick membership toggles; **Manage Recipe Books** opens the full picker.

Escape closes the panel. Tab stays inside the open panel, and closing restores focus to the originating control when that control remains available.

## Add ingredients to shopping lists

From a Book, choose all Meals or select a subset. The confirmation panel shows the number of recipes and offers a new list or an existing list. New lists let you choose servings; existing lists retain their serving setting. Saved Meals also expose shopping-list actions independently of Books.

Selected recipes are checked against household dietary allergies immediately before assembly. Mushroom protection remains included. Ingredient text screening cannot guarantee allergy safety: check ingredients, product labels, and the original recipe before cooking.

## Shared updates

Book and membership changes refresh other connected clients, including an already-open Book detail. If the event connection is unavailable, periodic refresh remains available. Search and selection are local UI state; the saved library and Book memberships are shared.

## Data and compatibility

Stable canonical Meal IDs are separate from legacy imported-recipe and list identifiers. Historical shopping lists remain snapshots, not an alternate recipe library. Missing source references must not silently resolve to a different Meal with a matching title.

Back up the application's persistent data before upgrading. Keep backups and operator-specific deployment notes outside public source control.
