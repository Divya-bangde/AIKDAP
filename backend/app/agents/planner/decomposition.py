"""Query decomposition for multi-subject (comparison) research questions.

One search against a question spanning several papers lets the paper
that best matches the wording take every result slot: "How does RAG
differ from GPT-3's few-shot prompting?" retrieved five RAG chunks and
no GPT-3 chunk at all. Splitting the question into one focused search
per subject, each with its own small result budget, gives every subject
its own evidence.

The model's answer is plain `<search>...</search>` tags, not JSON: the
local Qwen model has repeatedly broken JSON (unterminated strings, runaway
lists), while a tag list survives even a cut-off response — every tag
closed before the cut is still usable. Any failure falls back to the
original question, so decomposition can never make retrieval worse than
the single search it replaces.
"""

import re

from app.core.config import settings
from app.core.llm.gateway import LLMGateway
from app.core.logging.logger import get_logger

logger = get_logger(__name__)

#: Most searches one question is split into; bounds latency and context.
MAX_SUB_QUERIES = 4

_SEARCH_TAG = re.compile(r"<search>(.*?)</search>", re.IGNORECASE | re.DOTALL)

_SYSTEM_PROMPT = (
    "You split a research question into short, independent search queries "
    "for a document search engine. Write one query per subject the question "
    "compares or relates, each naming its subject and the aspect asked "
    "about. Use only terms from the question; never add facts. Output only "
    f"the queries, at most {MAX_SUB_QUERIES}, each inside its own pair of "
    "search tags exactly as in the example, with nothing else."
)

_EXAMPLE = (
    "Question: Compare the training data of BERT and GPT-3.\n"
    "<search>BERT training data</search>\n"
    "<search>GPT-3 training data</search>\n\n"
)


def parse_sub_queries(text: str) -> list[str]:
    """Extract the `<search>` queries, de-duplicated, in order, capped."""
    queries: list[str] = []
    seen: set[str] = set()
    for match in _SEARCH_TAG.findall(text):
        query = " ".join(match.split())
        if query and query.lower() not in seen:
            seen.add(query.lower())
            queries.append(query)
    return queries[:MAX_SUB_QUERIES]


async def decompose_query(query: str, gateway: LLMGateway) -> tuple[list[str], dict[str, object]]:
    """Split `query` into focused sub-queries; `[query]` when it cannot.

    Fewer than two sub-queries means there was nothing to split, so the
    original question is searched as-is.
    """
    try:
        # Pinned to the local Qwen with thinking off, like document
        # understanding: through the default chain a cloud reasoning model
        # answered and spent the whole token budget reasoning, cut off
        # before writing a single tag.
        response = await gateway.generate(
            prompt=f"{_EXAMPLE}Question: {query}\n",
            system_prompt=_SYSTEM_PROMPT,
            model=f"ollama_chat/{settings.qwen_model}",
            think=False,
            allow_fallback=False,
            temperature=0.0,
            max_tokens=200,
        )
    except Exception as exc:  # noqa: BLE001 -- any model failure falls back to one search
        logger.warning("query_decomposition_failed", error_type=type(exc).__name__)
        return [query], {"decomposition_attempted": True, "sub_queries": [query], "error": type(exc).__name__}

    sub_queries = parse_sub_queries(response.content)
    if len(sub_queries) < 2:
        sub_queries = [query]
    logger.info("query_decomposition", sub_query_count=len(sub_queries), latency_ms=response.latency_ms)
    return sub_queries, {
        "decomposition_attempted": True,
        "sub_queries": sub_queries,
        "latency_ms": response.latency_ms,
    }
