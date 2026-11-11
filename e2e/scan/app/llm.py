"""Code for the scan-mode end-to-end run: it still sends a model Groq shut down."""

from groq import Groq

client = Groq()


def reply(prompt: str) -> str:
    answer = client.chat.completions.create(
        model="llama3-8b-8192",
        messages=[{"role": "user", "content": prompt}],
    )
    return answer.choices[0].message.content
