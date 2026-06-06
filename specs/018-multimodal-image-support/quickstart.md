# Quickstart: Multimodal Image Support

**Branch**: `018-multimodal-image-support`

## Prerequisites

- `make up-safety` (brings up core + guardrails service)
- `make seed-kong` (Kong routes configured)
- `PLATFORM_KEY` set to a valid Kong consumer API key

## Send your first vision request

### URL image

```bash
curl -s -X POST http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $PLATFORM_KEY" \
  -d '{
    "model": "gpt-4o",
    "messages": [{
      "role": "user",
      "content": [
        {"type": "text", "text": "Describe what you see."},
        {"type": "image_url", "image_url": {"url": "https://upload.wikimedia.org/wikipedia/commons/thumb/4/47/PNG_transparency_demonstration_1.png/240px-PNG_transparency_demonstration_1.png"}}
      ]
    }]
  }' | jq '.choices[0].message.content'
```

### Base64 image

```bash
IMAGE_B64=$(curl -s https://upload.wikimedia.org/wikipedia/commons/thumb/4/47/PNG_transparency_demonstration_1.png/240px-PNG_transparency_demonstration_1.png | base64 | tr -d '\n')

curl -s -X POST http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $PLATFORM_KEY" \
  -d "{
    \"model\": \"gpt-4o\",
    \"messages\": [{
      \"role\": \"user\",
      \"content\": [
        {\"type\": \"text\", \"text\": \"What is this?\"},
        {\"type\": \"image_url\", \"image_url\": {\"url\": \"data:image/png;base64,${IMAGE_B64}\", \"detail\": \"low\"}}
      ]
    }]
  }" | jq '.choices[0].message.content'
```

## Verify non-vision model is rejected

```bash
curl -s -w "\nHTTP %{http_code}" -X POST http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $PLATFORM_KEY" \
  -d '{
    "model": "command-r-plus",
    "messages": [{
      "role": "user",
      "content": [
        {"type": "text", "text": "What is this?"},
        {"type": "image_url", "image_url": {"url": "https://example.com/img.jpg"}}
      ]
    }]
  }'
# Expected: HTTP 400 with error: "vision_model_required"
```

## Run smoke tests

```bash
make smoke
```

All existing smoke tests plus the two new multimodal cases must pass.

## Vision-capable models

| Model | Provider |
|---|---|
| `gpt-4o` | OpenAI |
| `gpt-4o-mini` | OpenAI |
| `gpt-4.1` | OpenAI |
| `claude-sonnet` | Anthropic |
| `gemini-flash` | Google |
| `gemini-pro` | Google |

## `detail` field values

| Value | Behaviour |
|---|---|
| `"auto"` | Provider chooses fidelity (default when omitted) |
| `"low"` | Lower resolution tile; faster and cheaper |
| `"high"` | Full resolution; higher token cost |

## Known limitations (v1)

- `stream: true` is not supported with image parts. Use non-streaming mode.
- Maximum 5 images per request (configurable via `MAX_IMAGES_PER_REQUEST` env var on Guardrails).
- Supported MIME types: `image/jpeg`, `image/png`, `image/gif`, `image/webp`.
- HTTP (non-TLS) image URLs are rejected.
