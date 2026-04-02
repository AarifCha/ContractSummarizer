ALL_SECTIONS_PROMPT = """Extract all critical construction contract data from the text below. Prioritize completeness.

RULES:
1. Use ONLY provided text. No inferences.
2. Omit categories completely if not present.
3. Quote high-risk legal language exactly.
4. CITATIONS REQUIRED: Append all source chunk IDs to the end of every extracted bullet, formatted exactly like this: [chunk_<id1>, chunk_<id2>].
5. ANTI-INDEX RULE: If you encounter a Drawing Log, Specification Index, or Table of Contents, DO NOT transcribe the list. Summarize it in one bullet (e.g., "Contains Drawing Log for Architectural and MEP sheets.").

CATEGORIES TO EXTRACT (If present):
## 1. Metadata: Parties, project info, core dates.
## 2. Financial: Pay-if-paid, condition precedent, retainage, billing, waivers, backcharges.
## 3. Schedule: Milestones, time is of the essence, liquidated damages, delay claims.
## 4. Risk: Broad indemnity, insurance limits, defense obligations, employee injury.
## 5. Scope: Work included, exclusions, referenced exhibits.
## 6. Legal: Termination, disputes, governing law, unusual pass-throughs.

TEXT:
{concatenated_5_page_text_with_chunk_ids}
"""

ALL_SECTIONS_WINDOW_MODEL = "gemini-2.5-flash-lite"
WINDOW_CONCURRENCY = 20
WINDOW_FETCH_MAX_ATTEMPTS = 4
