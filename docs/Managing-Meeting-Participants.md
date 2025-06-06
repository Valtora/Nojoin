This guide explains how to use the "Manage Participants" dialog to correct, merge, and manage the speakers identified in a meeting recording. Accurate speaker labels are key to generating useful meeting notes and a clean transcript.

#### How to Open the Dialog

1.  In the main window, right-click on the meeting you want to edit.
2.  Select **"Manage Participants"** from the context menu.

#### Understanding the Interface

When you open the dialog, you will see a list of all speakers identified in that recording. Each row has several controls:

*   ▶️ **Play/Stop Button:** Plays a short, representative audio snippet of the speaker's voice. This helps you identify who the speaker is.
*   🗑️ **Delete Button:** Deletes the speaker and all their associated transcript segments from this recording.
*   **Name Field:** An editable text box showing the speaker's current name. You can type here to rename them. As you type, you'll see suggestions from your Global Speaker Library.
*   🔗 **Linked Icon:** This icon appears if the participant is linked to a name in your Global Speaker Library, indicating a confirmed identity.

#### Key Actions

**1. Renaming a Speaker and Linking to the Global Library**

This is the most common action. When you rename a generic label like `SPEAKER_01` to a real name, Nojoin helps you maintain consistency.

*   **To Rename:** Simply click on the name field, type the correct name, and press `Enter` or click away. The change is saved automatically.
*   **Auto-linking to an Existing Global Speaker:** If you type a name that already exists in your Global Speaker Library (e.g., "John Doe"), a dialog will appear asking if you want to link this participant to the existing global profile. This is useful for ensuring the same person is correctly labeled across all meetings.
*   **Adding a New Global Speaker:** If you type a new, unique name, a dialog will ask if you'd like to add it to your Global Speaker Library. Adding it makes it easier to identify this person in future recordings.
*   **Using Suggestions:** As you type, a dropdown list of suggestions from your Global Library will appear. Clicking a name from this list will automatically rename the participant and link them to that global profile.

**2. Merging Two Speakers**

Sometimes, one person may be incorrectly identified as two different speakers (e.g., `SPEAKER_01` and `SPEAKER_03`). You can merge them into a single speaker.

1.  Click the **"Enable Merge Mode"** button. Checkboxes will appear next to each speaker.
2.  Select the checkboxes for the speakers you want to merge. You must select at least two.
3.  Click the **"Merge Selected"** button.
4.  A dialog will ask you to choose which speaker to merge *into*. All transcript segments from the other selected speakers will be reassigned to this target speaker.

**3. Deleting a Speaker**

If a speaker is irrelevant or was identified in error, you can remove them.

1.  Click the 🗑️ trash icon next to the speaker you want to remove.
2.  A confirmation dialog will appear.
3.  **Important:** If the speaker is linked to a global profile, the confirmation will clarify that this action only removes the speaker from *this recording*. The global profile will not be deleted.

**4. Saving Your Changes**

*   Name changes are saved automatically as you make them.
*   When you are finished, click **"Save"**.
*   If the **"Regenerate meeting notes after saving"** checkbox is ticked, the AI-powered notes will be re-generated using the updated speaker names, leading to more accurate summaries. 