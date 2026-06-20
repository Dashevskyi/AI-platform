"""Harvest real user questions from platform logs into a reviewable eval corpus.

Phase-1 of the SFT ladder: build the test set FROM REALITY, not invention.
Pulls distinct user messages for a tenant, drops noise (phones / numbers / math /
too short), and attaches a candidate tool (top semantic match via search_tools,
NO LLM) + score. Output is a TSV the human reviews: keep/fix the expected tool,
mark hold-out. Reviewed rows then graduate into eval_harness CASES.

Run:  PYTHONPATH=. venv/bin/python scripts/eval_corpus_harvest.py > /tmp/eval_corpus_draft.tsv
"""
import asyncio
import re
import sys

from sqlalchemy import text

from app.core.database import engine, async_session
from app.services.tools.embedder import search_tools

TENANT = "403d219f-0f4a-4782-a884-0e25f8bfe241"
EMBEDDING_MODEL = "bge-m3:latest"

# Noise filters — drop rows that aren't real intents.
_PHONE_OR_NUM = re.compile(r"^[\d\s()+\-.,:/]{3,}$")          # phone / number / time only
_MATH = re.compile(r"^\s*\d+\s*[+\-*/]\s*\d+\s*\??\s*$")       # "2+2?"
_PROFANITY = re.compile(r"\b(бл[яa]ть|сук[аи]|х[уy]й|пизд)", re.I)


def is_noise(q: str) -> bool:
    s = (q or "").strip()
    if len(s) < 8 or len(s) > 200:
        return True
    if _PHONE_OR_NUM.match(s) or _MATH.match(s):
        return True
    if _PROFANITY.search(s):
        return True
    # mostly non-letters → junk
    letters = sum(ch.isalpha() for ch in s)
    return letters < max(4, len(s) // 3)


async def main():
    async with engine.connect() as c:
        evk = [r[0] for r in (await c.execute(text(
            "SELECT id FROM tenant_api_keys WHERE tenant_id=:t AND name LIKE '\\_\\_eval%' ESCAPE '\\'"
            " OR name='__diag_op_key__'"), {"t": TENANT})).all()]
        rows = (await c.execute(text(
            """SELECT DISTINCT ON (lower(trim(m.content))) m.content
               FROM messages m JOIN chats ch ON ch.id=m.chat_id
               WHERE m.tenant_id=:t AND m.role='user'
                 AND (ch.api_key_id IS NULL OR ch.api_key_id <> ALL(:e))
               ORDER BY lower(trim(m.content)), m.created_at DESC"""),
            {"t": TENANT, "e": evk or ["00000000-0000-0000-0000-000000000000"]})).all()

    # Collapse whitespace (tabs/newlines inside a message would break the TSV).
    questions = sorted({" ".join((r[0] or "").split()) for r in rows if not is_noise(r[0])})

    # Attach a candidate tool (top semantic match, tenant-wide). Human reviews.
    labelled = []
    async with async_session() as db:
        for q in questions:
            res = await search_tools(tenant_id=TENANT, query=q, db=db,
                                     embedding_model=EMBEDDING_MODEL, top_k=2)
            top = res[0].name if res else ""
            score = round(getattr(res[0], "_semantic_score", 0.0), 3) if res else 0.0
            second = res[1].name if len(res) > 1 else ""
            second_sc = round(getattr(res[1], "_semantic_score", 0.0), 3) if len(res) > 1 else 0.0
            labelled.append((q, top, score, second, second_sc))

    labelled.sort(key=lambda x: (x[1], -x[2]))
    # expect_tool is PRE-FILLED with the candidate — reviewer corrects in place.
    # flag = LOW when the pick is shaky (weak score or close runner-up) → review first.
    # TSV: question, candidate_tool, score, runner_up, gap, flag, expect_tool(prefilled), holdout
    print("question\tcandidate_tool\tscore\trunner_up\tgap\tflag\texpect_tool\tholdout")
    for q, top, score, second, second_sc in labelled:
        gap = round(score - second_sc, 3)
        flag = "LOW" if (score < 0.5 or gap < 0.05) else ""
        print(f"{q}\t{top}\t{score}\t{second}\t{gap}\t{flag}\t{top}\t")

    # Histogram to stderr so it doesn't pollute the TSV.
    from collections import Counter
    hist = Counter(x[1] for x in labelled)
    print(f"\n# {len(labelled)} candidate questions (from {len(questions)} after noise filter)",
          file=sys.stderr)
    for name, n in hist.most_common():
        print(f"#   {n:3d}  {name}", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())
