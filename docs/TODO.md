# Nojoin To-Do List

## Audio Processing

- Currently when proxy audio is processing after a meeting has finished processing, the audio playback is correctly disabled.
- When the proxy audio processing finishes, the audio playback should be enabled without the user having to refresh the page as they currently have to do.
- Investigate how the proxy audio processing completion is triggered and update the frontend UI to reflect the change in state.

## TLS Certificate Validation

- Disabling TLS certificate validation potentially allows for MITM attacks but for users that self-host or otherwise deploy Nojoin remotely self-signing a certificate is the only choice.
- Nojoin is opinionated and it is our position that SSL is a poor security feature in general and largely obsolete.
- Let's discuss alternative security measures while leaving the current support for self-signed certificates intact.
