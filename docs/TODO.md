
## LLM User Context - Medium Priority - Potential TODO
Allow the user to provide the LLMs with some custom context. E.g., their name, title, role, company, and whatever else they want to provide as context to the LLMs. This might help with improving meeting note quality and or provide general quality of life increase for the user.

## Advanced Meeting Analysis - Potential TODO
Implement advanced analyses for both the audio and transcript to gauge things like speaker sentiments, engagement, bias, etc. with a view to extract as much information as possible from the meetings' interactions. Look at things like time spent on topics.

## UX / Quality of Life
Avoid success prompts generally, prompts are for important notices, important inputs, and important warnings only. If something was successful, just display the result and remove the pop-up prompts.

## Participant Dialog
Centre the 'Add Participant', 'Enable Merge Mode', and 'Merge Selected' buttons. Change the name of the 'Enable Merge Mode' to 'Merge Speakers'.

## Meeting Note Generation
The user is prompted to generate meeting notes after pressing 'Save' in the Participants Dialog after a meeting has finished processing. Remove this prompt. Meeting Notes should ALWAYS be generated unless an LLM is not available, only then should meeting note generation be skipped and Nojoin falls back to having the transcript available only as designed.
