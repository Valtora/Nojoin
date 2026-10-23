"""The start of every ffmpeg and ffprobe command line.

Each run reads local files named on its command line, so their inputs may open
the file protocol and no other.
"""

FFMPEG = ("ffmpeg", "-protocol_whitelist", "file")
FFPROBE = ("ffprobe", "-protocol_whitelist", "file")
