# Legal Disclaimer and Terms of Use

The canonical legal disclaimer for Nojoin. Every user accepts sections 1 and 2 for themselves before they can use Nojoin: the owner on the first step of the setup wizard, and everyone else the first time they sign in, whether they joined through an invitation or an administrator created their account. Until a user accepts, Nojoin refuses to start a recording or import audio for them. Nojoin records when each user accepted, and administrators see the date in **Settings > Users and access** ([ADMIN.md](ADMIN.md#terms-of-use)).

Accounts that existed before Nojoin 3.2.0 are asked once, the next time they open it. The account that set the installation up accepted during setup and is not asked again, but no date was recorded for it.

The setup wizard also tells the owner:

> As the owner, you are also responsible for the people you give accounts to, who each accept these terms the first time they sign in.

<!-- The app shows sections 1 and 2, the closing sentence and the owner's
sentence above from frontend/src/lib/legalTerms.json. scripts/validate_docs.py
fails when this page and that file differ, so change both together. -->

## 1. Compliance With Laws

You acknowledge that many legal jurisdictions require the consent of all parties before a conversation can be recorded. It is your sole responsibility to ensure compliance with all applicable laws and regulations regarding audio recording and transcription in your jurisdiction.

## 2. Data Privacy and Local Processing

Nojoin is designed with a privacy-first architecture.

- Your meeting data (audio, transcripts, meeting notes, chat, uploaded documents, speaker voiceprints, and calendar content) is never stored or transmitted to third parties without your explicit consent.
- All audio processing (transcription, diarisation, and similar) is performed locally on your machine or your self-hosted server, unless you explicitly configure an external provider.

## 3. Anonymous Usage Data

Separately from your meeting data, Nojoin sends a daily heartbeat containing a random number generated on your own server and the Nojoin version, so installations can be counted. It can also send anonymous usage diagnostics every six hours describing how the installation is configured and how much it is used, identified by the same random number. Neither contains the meeting data listed above, names, hostnames or addresses, or credentials, and the data is never sold.

Diagnostics can be switched off in **Settings > Privacy**. Both are disabled permanently with the `NOJOIN_TELEMETRY_ENABLED` environment variable, or blocked at the network layer. [TELEMETRY.md](TELEMETRY.md) documents exactly what is sent, when, how long it is kept, and how to verify it.

## 4. Liability

By using Nojoin, you acknowledge that you will use this software in a lawful manner. The developers of Nojoin assume no liability for any unlawful use of this application.

By proceeding, you agree to these terms and accept full responsibility for the lawful use of this software.
