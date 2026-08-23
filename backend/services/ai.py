import os
import httpx

# Both providers retire model IDs without notice — keep them overridable.
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-flash-latest")


async def call_ai(system: str, user: str) -> str:
    try:
        from groq import AsyncGroq
        client = AsyncGroq(api_key=os.getenv("GROQ_API_KEY"))
        response = await client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user}
            ],
            max_tokens=1024,
            temperature=0.7,
        )
        return response.choices[0].message.content

    except Exception as groq_err:
        print(f"⚠️ Groq failed: {groq_err}, falling back to Gemini...")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
        payload = {
            "contents": [{"parts": [{"text": f"{system}\n\n{user}"}]}]
        }
        async with httpx.AsyncClient() as client:
            # key goes in a header, not the URL, so it stays out of tracebacks
            r = await client.post(
                url,
                json=payload,
                headers={"x-goog-api-key": os.getenv("GEMINI_API_KEY", "")},
                timeout=20,
            )
        if r.status_code != 200:
            raise RuntimeError(f"Gemini request failed ({r.status_code}): {r.text[:300]}")
        return r.json()["candidates"][0]["content"]["parts"][0]["text"]
