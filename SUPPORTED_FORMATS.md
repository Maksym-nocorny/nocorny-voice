# Supported Media Formats

The bot supports transcription of various audio and video formats through Google Gemini API.

## Supported Audio Formats

The following audio formats are supported:

| Format | MIME Type | Extension |
|--------|-----------|-----------|
| AAC | `audio/aac` | `.aac` |
| FLAC | `audio/flac` | `.flac` |
| MP3 | `audio/mp3`, `audio/mpeg` | `.mp3` |
| M4A | `audio/m4a` | `.m4a` |
| MPGA | `audio/mpga` | `.mpga` |
| MP4 Audio | `audio/mp4` | `.mp4` |
| Opus | `audio/opus` | `.opus` |
| PCM | `audio/pcm` | `.pcm` |
| WAV | `audio/wav` | `.wav` |
| WebM Audio | `audio/webm` | `.webm` |
| OGG Vorbis | `audio/ogg` | `.ogg` |

**Telegram Voice Messages**: Automatically sent as `audio/ogg` format.

## Supported Video Formats

The following video formats are supported:

| Format | MIME Type | Extension |
|--------|-----------|-----------|
| FLV | `video/x-flv` | `.flv` |
| **MOV** | `video/quicktime` | `.mov` |
| MPEG | `video/mpeg` | `.mpeg` |
| MPEGPS | `video/mpegps` | `.mpg` |
| MPG | `video/mpg` | `.mpg` |
| MP4 | `video/mp4` | `.mp4` |
| WebM | `video/webm` | `.webm` |
| WMV | `video/wmv` | `.wmv` |
| 3GPP | `video/3gpp` | `.3gp` |

**Telegram Video Notes**: Automatically sent as `video/mp4` format (circular videos).

## File Size Limits

- **Maximum file size**: 20 MB (Gemini API inline data limit)
- **Maximum audio length**: ~8.4 hours (1 million tokens)
- **Maximum video length**: ~45 minutes with audio, ~1 hour without audio

## How Telegram Sends Files

When you send media to the bot:

1. **Voice messages** → Sent as `voice` type with `audio/ogg` MIME type
2. **Video notes** (circular videos) → Sent as `video_note` type with `video/mp4` MIME type
3. **Audio files** → Sent as `audio` type with original MIME type (MP3, M4A, etc.)
4. **Video files** (including MOV) → Sent as `video` type with original MIME type

## Troubleshooting

If a file doesn't transcribe:

1. **Check file size**: Files over 20 MB won't work with inline data
2. **Check format**: Ensure the format is in the supported list above
3. **Check logs**: Look at Supabase Edge Function logs for error details
4. **MIME type detection**: The bot logs the detected MIME type for debugging

### Common Issues

- **MOV files**: Should work automatically as Telegram sends them with `video/quicktime` MIME type
- **Large files**: If your file is over 20 MB, try compressing it first
- **Unsupported codecs**: Some video files may have unsupported audio/video codecs even if the container format is supported

## Testing

To test format support:
1. Send a media file to the bot
2. Check Supabase logs for: `Video detected - MIME type: <type>`
3. If transcription fails, check the error message in logs
