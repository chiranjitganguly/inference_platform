import asyncio
import json
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

ENDPOINT = "http://localhost:8080/v1/chat/completions"


async def main() -> None:
    headers = {
        "Authorization": os.environ["PLATFORM_API_KEY"],
        "Content-Type": "application/json",
    }
    payload = {
        "model": "claude-sonnet",
        "max_tokens": 1024,
        "messages": [{"role": "user", "content": "What is the curriculam for a machine learning course for a software developer? Give elaborate response."}],
        "stream": True,
        "guardrails": False,
    }

    async with httpx.AsyncClient(timeout=60) as client:
        async with client.stream("POST", ENDPOINT, headers=headers, json=payload) as response:
            async for line in response.aiter_lines():
                if line.startswith("data: ") and line != "data: [DONE]":
                    chunk = json.loads(line[6:])
                    if "error" in chunk:
                        print(f"\n[error] {chunk['error']['message']}", flush=True)
                        break
                    choices = chunk.get("choices", [])
                    if choices:
                        delta = choices[0]["delta"].get("content", "")
                        if delta:
                            print(delta, end="", flush=True)
    print()


asyncio.run(main())
