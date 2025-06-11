
## Meeting Name Inference - Low Priority - Potential TODO
Implement a first-pass attempt at inferring a title for the meeting based on the context within the transcript.

## LLM User Context - Medium Priority - Definite TODO
Allow the user to provide the LLMs with some custom context. E.g., their name, title, role, company, and whatever else they want to provide as context to the LLMs. This might help with improving meeting note quality and or provide general quality of life increase for the user.

## Advanced Meeting Analysis - Potential TODO
Implement advanced analyses for both the audio and transcript to gauge things like speaker sentiments, engagement, bias, etc. with a view to extract as much information as possible from the meetings' interactions. Look at things like time spent on topics.

## Codebase Polish - Definite TODO
Prune the codebase of unnecessary or overly verbose comments. Especially where code has been commented out and is now obsolete.

## Whisper Model Download
Update the progress dialog to tell the user it is downloading a new model if it is being run for the first time. Show this progress in a separate new dialog just for this purpose called 'Model Download Dialog'. Also centre the time elapsed counter and remove the 'Stage:' label and status update as there is already. Finally, let's prompt the user to download the default model on first-run if its not detected. They can of course bypass this and download on first transcription but let's offer the option.