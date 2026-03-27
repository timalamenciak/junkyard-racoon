# Literature API Investigation: OpenAlex, CrossRef, Scopus, Web of Science

This document sketches how a `ingest/literature_search.py` step could pull new papers directly from academic APIs, supplementing the current RSS-feed approach.

---

## API comparison

| API | Cost | Auth needed | Coverage | Rate limit | Best for |
|-----|------|-------------|----------|------------|----------|
| **OpenAlex** | Free | No (polite pool via `mailto=`) | 250M+ works, very good | 100k req/day | First choice — rich, free, well-structured |
| **CrossRef** | Free | No (polite pool via `mailto=`) | 150M+ DOIs | ~50 req/sec | DOI resolution, citation data |
| **Scopus** | WLU subscription | API key from Elsevier | Very comprehensive | 6 req/sec | High-quality filtered search |
| **Web of Science** | WLU subscription | API key separate from WLU login | Gold standard citation data | 5 req/sec | Citation analysis, if access granted |

**Recommended starting point: OpenAlex + CrossRef.** Both are free, no institutional approval needed, and together cover the vast majority of ecology and conservation literature. Add Scopus if the library can provision an API key.

---

## OpenAlex

Documentation: https://docs.openalex.org

### Getting started

No registration required. Add `?mailto=your@email.com` to requests to get into the polite pool (faster, more reliable).

```python
import requests

BASE = "https://api.openalex.org"
MAILTO = "tim@wlu.ca"

def search_openalex(keywords: list[str], from_date: str, per_page: int = 50) -> list[dict]:
    """Search OpenAlex for recent works matching any of the keywords."""
    query = " OR ".join(f'"{kw}"' for kw in keywords)
    resp = requests.get(
        f"{BASE}/works",
        params={
            "search": query,
            "filter": f"from_publication_date:{from_date},is_retracted:false",
            "sort": "publication_date:desc",
            "per-page": per_page,
            "mailto": MAILTO,
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("results", [])
```

### Useful filters

```
filter=concepts.id:C2778793908              # ecology concept ID
filter=topics.id:T10037                     # restoration ecology topic
filter=authorships.institutions.country_code:CA  # Canadian authors only
filter=open_access.is_oa:true               # open access only
filter=from_publication_date:2026-03-01     # published since date
```

### Mapping to pipeline schema

```python
def openalex_to_article(work: dict) -> dict:
    authors = [a["author"]["display_name"] for a in work.get("authorships", [])[:3]]
    return {
        "title": work.get("display_name", ""),
        "authors": authors,
        "link": work.get("doi") or work.get("id", ""),
        "published": work.get("publication_date", ""),
        "abstract": (work.get("abstract_inverted_index") or {}).keys(),  # see note below
        "journal": (work.get("primary_location") or {}).get("source", {}).get("display_name", ""),
        "doi": work.get("doi", ""),
        "open_access": work.get("open_access", {}).get("is_oa", False),
        "source": "openalex",
    }
```

> **Note on abstracts:** OpenAlex stores abstracts as inverted indexes (word → position list) for licensing reasons. Reconstruct with:
> ```python
> def reconstruct_abstract(inverted: dict) -> str:
>     words = {pos: word for word, positions in inverted.items() for pos in positions}
>     return " ".join(words[i] for i in sorted(words))
> ```

---

## CrossRef

Documentation: https://www.crossref.org/documentation/retrieve-metadata/rest-api/

### Getting started

```python
def search_crossref(keywords: list[str], from_date: str, rows: int = 50) -> list[dict]:
    query = " ".join(keywords[:5])  # CrossRef query is simpler than OpenAlex
    resp = requests.get(
        "https://api.crossref.org/works",
        params={
            "query": query,
            "filter": f"from-pub-date:{from_date}",
            "sort": "published",
            "order": "desc",
            "rows": rows,
            "mailto": MAILTO,
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("message", {}).get("items", [])
```

CrossRef works best for **DOI resolution** (given a DOI, get full metadata) rather than broad keyword search. Use it to enrich OpenAlex results with citation counts or to verify DOIs from RSS.

---

## Scopus (Elsevier)

Documentation: https://dev.elsevier.com/documentation/SCOPUSSearchAPI.wadl

### Getting an API key

1. Go to https://dev.elsevier.com
2. Create an account using your WLU email
3. Create a new application → request `SCOPUS SEARCH` access
4. The library may need to approve institutional access — email them first

### Basic search

```python
def search_scopus(keywords: list[str], api_key: str, from_date: str, count: int = 25) -> list[dict]:
    query = " AND ".join(f'TITLE-ABS-KEY("{kw}")' for kw in keywords[:4])
    resp = requests.get(
        "https://api.elsevier.com/content/search/scopus",
        headers={"X-ELS-APIKey": api_key, "Accept": "application/json"},
        params={
            "query": query,
            "date": f"{from_date[:4]}-{datetime.date.today().year}",
            "sort": "coverDate",
            "count": count,
        },
        timeout=30,
    )
    resp.raise_for_status()
    entries = resp.json().get("search-results", {}).get("entry", [])
    return entries
```

Set `SCOPUS_API_KEY` as an environment variable. Do not hardcode.

---

## Web of Science

WoS API access is the hardest to get. Check with the WLU library — even if you have database access, the API requires a separate institutional agreement with Clarivate.

The **WoS Starter API** (free tier) allows 1 req/sec and limited fields:

```
https://api.clarivate.com/apis/wos-starter/v1/documents
```

For most use cases, OpenAlex is a better free alternative.

---

## Proposed pipeline integration

### New file: `power-tools/ingest/literature_search.py`

```
ingest/literature_search.py
  reads: configs/journals.yaml (existing, for keywords + topics)
         configs/lab_profile.yaml (for research_interests)
  writes: data/ingest/literature_search.json   ← new
  state:  data/state/literature_seen.json      ← dedup by DOI/OpenAlex ID
```

The existing `rss_journals.py` would stay as-is (for journal-specific RSS monitoring). `literature_search.py` would be a **broader sweep** — searching by keyword/topic across all journals.

### nightly_run.py order

```
ingest/collaborator_publications.py
ingest/gmail_imap_bridge.py
ingest/rss_journals.py          ← existing, RSS-based
ingest/literature_search.py     ← NEW: API-based broad sweep
ingest/grant_opportunities.py
...
processing/score_articles.py    ← already merges multiple ingest sources
```

`score_articles.py` would need a small update to also load `literature_search.json` and merge it with `journal_articles.json` before scoring — or `literature_search.py` can append directly to `journal_articles.json`.

---

## Implementation priority

1. **OpenAlex** — implement first. Zero cost, excellent API, no approval needed. Delivers 80% of the value.
2. **CrossRef** — add for DOI enrichment once OpenAlex is working.
3. **Scopus** — add if library can provision an API key. Best for confirming nothing is missed.
4. **Web of Science** — add only if Clarivate grants API access; treat as a nice-to-have.

### Recommended keywords for OpenAlex queries (start tight, expand)

```yaml
literature_search_keywords:
  - restoration ecology
  - ecological restoration
  - biodiversity conservation
  - community-based conservation
  - knowledge mobilization ecology
  - human dimensions conservation
  - conservation social science
  - AI ecology
  - machine learning conservation
  - evidence synthesis ecology
```

These map directly to `research_interests` in `lab_profile.yaml` — the implementation should read from there.

---

## Next steps

- [ ] Confirm WLU library can provision a Scopus API key
- [ ] Implement `literature_search.py` with OpenAlex
- [ ] Add dedup logic (DOI-based, same as `rss_seen_articles.json`)
- [ ] Wire into `score_articles.py` merge step
- [ ] Evaluate CrossRef enrichment after first week of OpenAlex results
