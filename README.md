# DocLang Reingestion Savings

**Related projects**

| Project | What it is | How this demo uses it |
|---------|------------|------------------------|
| [DocLang](https://github.com/doclang-project/doclang) ([doclang.ai](https://www.doclang.ai/)) | AI-native document format (spec + toolkit). Keeps structure, semantics, and layout in a form that works well for LLM / RAG pipelines. | We treat prepared DocLang (`.dclx`) as the **durable intermediate**: load → chunk → count tokens → estimate embedding cost. We do not re-extract the PDF. |
| [Docling](https://github.com/docling-project/docling) (open source) / [Docling for IBM watsonx](https://www.ibm.com/products/docling) (SaaS) | Document conversion for gen AI. OSS toolkit prepares PDFs and other files; the managed service is priced from **USD 4 per 1,000 pages**. | **Docling SaaS** is the priced extraction step (PDF → DocLang). We load archives with [`docling-core`](https://pypi.org/project/docling-core/) (`DoclingDocument.load_from_doclang_archive`) — no OCR or layout inference in this app. |

This demo is **aligned** with DocLang’s idea: once content is in DocLang, downstream work (chunking, embedding, indexing, evaluation) can reuse that representation instead of starting from the PDF again. We measure the **cost** of that story (extraction $ vs embedding $) and, in a second tab, **retrieval quality** on FinanceBench. We do not implement the DocLang validate/pack toolkit, and we do not run Docling extraction here.

---

This demo answers two questions:

1. **Cost:** If I convert a PDF to DocLang with [Docling SaaS](https://www.ibm.com/products/docling), how does that extraction cost compare to embedding the text afterward?
2. **Quality:** On FinanceBench, does DocLang retrieve the right pages better than a fast Unstructured baseline (OpenSearch k-NN)?

---

## Why DocLang helps (business context)

### The problem

Many companies keep large PDF archives that almost never change: old filings, policies, manuals, historical knowledge bases. For RAG and search, those PDFs still need to be turned into text, chunked, and embedded.

In practice, **most of the money goes to PDF extraction**, not to embeddings. On a long document you often see something like:

| Step | What happens | Share of combined $ (typical) |
|------|--------------|-------------------------------|
| Extraction | Cloud parser turns PDF → structured text / DocLang | **~99%** |
| Embedding | Create vectors from that text | **~1%** |

So if you change chunk size, overlap, or the embedding model later, a normal pipeline often **parses the PDF again**. You pay the expensive step over and over. That is the re-ingestion trap: for example about **$0.74–$2.79 per document** on paid layout parsers, plus waiting on the network — every time you iterate.

![Example UI: PDF extraction vs embedding cost, with why DocLang helps](docs/cost-comparison-results.png)

### The idea: parse once, reuse DocLang

[Docling](https://www.ibm.com/products/docling) / [DocLang](https://www.doclang.ai/) gives you a prepared intermediate file (`.dclx`). You pay for layout/extraction **once**, keep that file, and later only re-chunk and re-embed locally.

```
[ PDF archive ] ── parse once ──> [ DocLang cache ] ── re-chunk / re-embed ──> [ vector DB / OpenSearch ]
                      │                     │                                      │
                 paid extraction      local load (~seconds)                 cheap embedding $
                 e.g. $0.74–$2.79     e.g. ~8.65 s on a 186-page filing    e.g. ~$0.004
```

For static PDFs, extraction stops being a recurring bill. It becomes a **one-time preparation cost**.

### What you get

| Advantage | In plain words |
|-----------|----------------|
| **No re-parse tax** | Same DocLang file; next runs skip Docling SaaS / other extraction vendors. Re-embedding a ~186-page filing can be about **$0.004** (`text-embedding-3-small`) instead of another full extraction bill. |
| **Faster, predictable prep** | Local load → chunk → tokenize. No cloud queue for parsing. Dense filings can finish local prep in a few seconds (example: **~8.65 s** for 186 pages in this demo). |
| **Easy to experiment** | Try a new embedding model, chunking, or index (including OpenSearch) without touching the PDF again. Useful for development, A/B tests, and rebuilds. |

### When do you reingest the same DocLang again?

The PDF did not change. Your ML / search choices often do — and each change needs a new pass over the text, **not** a new PDF extraction.

| Scenario | What you change | What you keep |
|----------|-----------------|---------------|
| **Different embedding model** | Switch provider, upgrade the model, or try a local model (e.g. Qwen) | Same DocLang |
| **Different chunking** | Chunk size, overlap, or table packing — often to improve retrieval | Same DocLang |
| **Rebuild / retarget the index** | Schema change, bad index, or a second search / RAG store (including OpenSearch) | Same DocLang |
| **Development and iteration** | Settings, prompts, wiring — you often re-run the same documents many times while building | Same DocLang |
| **A/B or evaluation** | Compare two or more embedding / chunking setups on a fixed document set and pick a production winner | Same DocLang |

**Without DocLang:** each of those runs ≈ extraction $ + embedding $.  
**With DocLang:** each run ≈ embedding $ only (extraction already paid).

This app estimates those dollars from real page and token counts. The UI lists the same scenarios under **Why DocLang helps**. The **Retrieval quality** tab then checks whether DocLang also helps search (FinanceBench + OpenSearch), not only cost.

---

## What this app is (and is not)

The app does **not** run Docling SaaS extraction. The DocLang files already exist. The app only:

1. Counts the **pages of the original PDF**. This gives the extraction price estimate.
2. Loads the prepared **DocLang** file, splits it into chunks, and counts **tokens**. This gives the embedding price estimate.
3. Shows both dollar numbers side by side, with the ratio.

By default no embeddings are generated. You only need token counts to estimate an embedding bill. A "full run" with local Qwen embeddings is optional.

| This app does | This app does not |
|---------------|-------------------|
| Read existing `.dclx` DocLang files | Extract text from PDFs with Docling |
| Count PDF pages (with pypdf, no OCR) | Run OCR or layout models |
| Chunk DocLang text and count tokens | Call OpenAI or any paid API |
| Estimate Docling SaaS $ vs embedding $ | Index a vector database or answer questions |

Think of it as a **cost calculator that uses real document size** (pages and tokens). It is not a full ingestion pipeline.

---

## How the comparison works

```
PDF      ──(page count only)──────────►  extraction $ = pages / 1000 × price_per_1000_pages
DocLang  ──(load → chunk → tokenize)──►  embedding $  = tokens / 1,000,000 × price_per_million_tokens
```

- **Pages** come from the matching PDF. If there is no PDF, the app uses the page list inside the DocLang file. You can also type the page count by hand.
- **Tokens** are counted per chunk, so they include the chunk overlap and repeated headings. This matches what you would really send to an embedding API.
- Two token counts are shown:
  - **Paid-model tokens** (tiktoken `cl100k_base` by default, which is the tokenizer of OpenAI `text-embedding-3-*`). This number drives the embedding $.
  - **Local Qwen tokens** (exact, from the `Qwen/Qwen3-Embedding-0.6B` tokenizer). These are for reference only.

With the defaults (Docling SaaS **$4 / 1,000 pages**, Unstructured.io **$15 / 1,000 pages**, Azure Document Intelligence Layout **$10 / 1,000 pages**, Snowflake AI_PARSE_DOCUMENT Layout global **$7.32 / 1,000 pages**, `text-embedding-3-small` at $0.02 / 1M tokens), Docling extraction is usually the cheapest of these layout-class options for the same page count, and every extraction option is still far larger than embedding for long filings.

---

## How chunking works (`chunking.py`)

Chunk settings change the token count, so it is useful to know what happens:

1. The DocLang document is exported to markdown (`export_to_markdown`).
2. The markdown is split into **headings**, **paragraphs** and **tables**.
3. Blocks are packed into chunks of at most `chunk_size` characters (default 1000).
4. Every new chunk starts with its **section heading**, so each chunk keeps its context.
5. Neighbouring chunks share the last `chunk_overlap` characters (default 150). The overlap can be at most half the chunk size.
6. Big tables are split into row groups. **Each group repeats the table header row.**
7. The column padding spaces from the markdown table export are removed, because they are not real content.

More overlap means more tokens, and so a higher embedding $. The difference is small compared to extraction.

---

### What happens when you click "Estimate cost"

1. The UI calls `GET /api/documents?source=local` (or `minio`). The backend lists the DocLang files and pairs each one with a PDF.
2. You select documents and click **Estimate cost (no embedding)**.
3. The UI calls `POST /api/jobs/process` with `skip_embedding: true`.
4. A background job in `jobs.py` runs, for each document:
   - find the `.dclx` on disk (or download it from MinIO, depending on source and run mode)
   - count PDF pages if the page count is not known yet (`pages.py`)
   - load the DocLang archive (`doclang_loader.py`)
   - chunk it (`chunking.py`)
   - count tokens (`tokens.py`)
   - calculate costs (`cost.py`)
5. The UI polls `GET /api/jobs/{id}` every 0.5 s. It shows live logs, then the results.
6. The result is saved to `backend/data/runs/<job_id>.json` and appears in **History**.

In cost-only mode the embedding model is never loaded. The **Qwen tokenizer** is still loaded to count local tokens. The first run downloads it from Hugging Face (about 10 MB).

If you choose **Full run**, step 4 also loads `Qwen/Qwen3-Embedding-0.6B` and generates vectors (`embeddings.py`). On CPU this can take many minutes for a large filing.

### Run modes

| Mode | Meaning |
|------|---------|
| **Staged** (default) | The DocLang file must already be on disk. Timing does not include download. |
| **Download inclusive** | Downloads from MinIO inside the measured run, so download time is included. |

If staged mode says "requires cached DocLang", click **Download selected** first, or switch the source to local disk.

---

## Data sources: MinIO and local disk

### Secrets live in `backend/.env` only

Put credentials and your real bucket settings in **`backend/.env`**. That file is gitignored.

| Put in `backend/.env` | Do not put in git / README |
|-----------------------|----------------------------|
| `MINIO_ENDPOINT` | Real hostnames |
| `MINIO_ACCESS_KEY` | Access keys |
| `MINIO_SECRET_KEY` | Secret keys / session tokens |
| `MINIO_BUCKET`, `MINIO_PREFIX` | Your real bucket and folder names |
| `MINIO_SECURE` | — |

`backend/.env.example` is a template with placeholders only. Copy it, then fill in real values:

```bash
cd backend
cp .env.example .env
# edit .env with your MinIO / COS credentials
```

The backend loads `backend/.env` on startup (`config.py`). Keys never go to the frontend; the UI only sees connection status (host, bucket, prefix, errors) — not access or secret keys.

### Object layout (example names)

Use whatever prefix you set in `.env`. A common layout:

| Path under your prefix | Role |
|------------------------|------|
| `dclx/` | Prepared DocLang (`.dclx`). Required. |
| `pdf/` | Original PDFs. Used only for the page count. |
| `json/` | Optional. Never used as a silent fallback when DocLang fails to load. |

Example placeholders (not real credentials):

```env
MINIO_ENDPOINT=minio.example.com:9000
MINIO_SECURE=true
MINIO_BUCKET=your-bucket
MINIO_PREFIX=docs/
MINIO_ACCESS_KEY=your-access-key
MINIO_SECRET_KEY=your-secret-key
```

MinIO access is **read-only**: the app only lists and downloads. It never writes or deletes remote objects.

You can download once, then use **Document source → Local disk**. After that MinIO is not needed:

```text
backend/data/cache/<your-prefix>/dclx/*.dclx
backend/data/cache/<your-prefix>/pdf/*.pdf
```

Or put copies under `backend/data/local/...` (`LOCAL_DOCS_DIR`). Downloads always come from MinIO, so the **Download** button is disabled for the local source.

---

## Setup

Requirements: Python 3.12, Node.js 20+.

### Backend

```bash
cd backend
cp .env.example .env
# Edit backend/.env — put real MINIO_* credentials there only (never commit .env)

python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

uvicorn app.main:app --reload --port 8000
```

After changing `.env`, click **Reload connection** in the UI (or restart uvicorn) so the backend re-reads it.
### Frontend (second terminal)

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:3000.

The frontend forwards `/api/*` to port 8000. **Both processes must be running.** If only the UI is running, you will see "Internal Server Error" and an empty Connection panel.

From the repo root you can also use `npm run backend`, `npm run frontend` and `npm test`.

---

## Demo walkthrough

1. Start the backend and the frontend.
2. Set **Document source** to **Local disk** (if you already downloaded) or **MinIO**.
3. Refresh the list and select one or more documents.
4. If the PDF match is ambiguous, pick the PDF by hand. You can also type the page count.
5. Keep **Run purpose → Cost estimate only (skip embedding)**.
6. In **PDF extraction vs. embedding cost**, check the prices:
   - Docling SaaS (default $4 / 1,000 pages)
   - Unstructured.io (default $15 / 1,000 pages = $0.015 / page)
   - Azure Document Intelligence Layout (default $10 / 1,000 pages)
   - Snowflake AI_PARSE_DOCUMENT Layout, global routing (default $7.32 / 1,000 pages)
   - Embedding model and price (default `text-embedding-3-small`, $0.02 / 1M tokens)
7. Click **Estimate cost (no embedding)**.
8. Read the three cards (extraction, embedding, total), the ratio sentence, and the **Why DocLang helps** box (without vs with reusable DocLang).
9. You can change the prices after the run. The costs and the business takeaway update instantly, with no new run.
10. Export JSON or CSV from the results section if you need it.

---

## Cost formulas (estimates, not bills)

```
docling_extraction   = page_count / 1000 × $4.00          # USD / 1,000 pages
unstructured_extract = page_count / 1000 × $15.00         # $0.015 / page
azure_layout         = page_count / 1000 × $10.00         # Azure DI Layout / prebuilt
snowflake_layout     = page_count / 1000 × $7.32          # AI_PARSE_DOCUMENT Layout, global
embedding_cost       = paid_model_tokens / 1,000,000 × embedding_price_per_million_tokens
total_with_docling   = docling_extraction + embedding_cost
ratio                = docling_extraction / embedding_cost
```

Azure Document Intelligence ([pricing](https://azure.microsoft.com/en-us/pricing/details/document-intelligence/)) also lists Read at $1.50 / 1,000 pages (0–1M) and custom extraction at $30 / 1,000 pages — edit the rate in the UI if you want those SKUs. Snowflake regional routing is $8.052 / 1,000 pages.
Notes:

- Prices are **your assumptions**. You can edit them in the UI. The app does not check real invoices.
- The cost estimate calls no paid API. Token counts are local estimates, and the UI shows which method was used. (Only the optional recall comparison below calls OpenAI, and only when you click its run button.)
- Storage, network and your own compute are **not** included.
- If a selected document has no page count, the run is marked **partial** and a warning says which document is missing. The extraction $ would be too low otherwise.
- If DocLang already exists, reusing it means you do not pay extraction again. The results page shows this in a small "reuse" section.

---

## Retrieval quality: DocLang vs Unstructured (FinanceBench)

The cost tab asks: “Is extraction expensive?”  
This tab asks: **“When we search, do we still find the right PDF page?”**

We use the same questions, the same chunk size, and the same embedding model.  
We only change how text was taken from the PDF:

| Pipeline | Where the text comes from |
|----------|---------------------------|
| **DocLang** | Prepared `.dclx` file (already extracted earlier) |
| **Unstructured** | Same PDF, open-source `partition_pdf` with strategy `fast` |

If DocLang finds the right page more often, better structure (headings, tables) is helping search — not a different chunker or model.

### Example question (from FinanceBench)

The open dataset has **150 questions** on **84 SEC filings**.  
Source: [FinanceBench](https://github.com/patronus-ai/financebench) ([paper](https://arxiv.org/abs/2311.11944)).  
We download `financebench_open_source.jsonl` once into `backend/data/recall/financebench/`.

One real row looks like this (shortened):

```json
{
  "financebench_id": "financebench_id_03029",
  "doc_name": "3M_2018_10K",
  "question_type": "metrics-generated",
  "question": "What is the FY2018 capital expenditure amount (in USD millions) for 3M? … cash flow statement.",
  "answer": "$1577.00",
  "evidence": [
    {
      "evidence_page_num": 59,
      "evidence_text": "… Purchases of property, plant and equipment … (1,577) …"
    }
  ]
}
```

How we use it:

1. The **question** text is what we search with.
2. **`doc_name`** says which filing holds the answer (`3M_2018_10K`).
3. FinanceBench stores pages as **0-based** (`59`). We turn that into **PDF page 60** (`+ 1`) so it matches the PDF, DocLang, and Unstructured. See `backend/app/recall/dataset.py`.
4. A search **hit** means: in the top chunks we returned, at least one chunk is from that document **and** that page (here: `3M_2018_10K`, page **60**).

Other question types in the file: `domain-relevant`, `novel-generated`. The UI can show scores per type.

### Simple picture

```
FinanceBench question
        │
        ▼
  embed the question  ──┐
                        │  find closest chunks
  PDF / DocLang text → chunks → embed chunks ──┘
                        │
                        ▼
              top chunks (each has a page number)
                        │
                        ▼
         is the labeled evidence page in the top k?
```

Code map (open these files to follow the flow):

| Step | What happens | File |
|------|----------------|------|
| Load questions | Read JSONL, page `+ 1` | `backend/app/recall/dataset.py` |
| DocLang → pages | Group items by page; keep headings/tables | `extract.py` → `doclang_pages` |
| Unstructured → pages | Group PDF elements by `page_number` | `extract.py` → `unstructured_pages` |
| Fix DocLang pages | If archive skipped a PDF page, remap | `extract.py` → `align_to_pdf_pages` |
| Chunk | Same chunker; **one page per chunk** | `extract.py` → `page_chunks` |
| Embed | OpenAI `text-embedding-3-large`, cache on disk | `embed.py` |
| Search + score | Top-k chunks, then Hit@k | `metrics.py`, `runner.py` |
| OpenSearch path | Same score idea, search with k-NN | `opensearch_store.py`, `evaluate_opensearch.py` |

### Two ways we search

**All filings together (global)**  
Search chunks from all 84 documents. Harder: the system must pick the right company and year, then the right page.

**One filing at a time (per-document)**  
Search only inside `3M_2018_10K` for that question. Easier: the document is already known.

In the UI, use the buttons **All filings together** / **One filing at a time**.

### What Hit@k means (same example)

We embed the question and find the closest chunks. Each chunk knows its page.

One page is often split into several chunks. If we ranked chunks directly, the same wrong page could take two or three of the top slots. So we keep only the **best chunk of each page** and rank **pages**. Top 5 always means 5 different pages. See `unique_pages` in `metrics.py`.

For question `03029`, the labeled page is **60**.

- DocLang returns pages like `47, 60, 61, …` → first good page at rank **2** → **Hit@3** and **Hit@5** (not Hit@1).
- If Unstructured first shows page 60 at rank **8** → Hit@5 = no, Hit@10 = yes.

**Hit@5** on the summary card means:  
“Out of 150 questions, how many had the right page somewhere in the top 5 pages?”

Full run (150 questions, OpenSearch):

| Search scope | | Top 1 | Top 5 | Top 10 |
|--------------|---|-------|-------|--------|
| All filings | DocLang | 24.0% | 50.7% | 66.0% |
| | Unstructured | 16.0% | 46.0% | 59.3% |
| One filing | DocLang | 35.3% | 76.0% | 87.3% |
| | Unstructured | 34.0% | 72.7% | 86.0% |

Example UI — **one filing at a time**, Top 5 (DocLang 76.0% vs Unstructured 72.7%):

![Retrieval quality UI: one filing, Top 5](docs/retrievalquality1.png)

Same run with **Top 10** (DocLang 87.3% vs Unstructured 86.0%). The explorer below shows questions only DocLang found:

![Retrieval quality UI: one filing, Top 10](docs/retrievalquality2.png)

Top 1 is low for both: vector search often puts a related page first (for `03029`, page 47 before page 60). Keyword search or a reranker would help Top 1; this demo uses vector search only.

~50% for “all filings” is normal when you only use vector search (no keyword search, no reranker) over 84 long filings.  
Always compare DocLang vs Unstructured **in the same scope**.

On this same question in a real run: across all filings, DocLang put page 60 at rank **3** (Hit@5 yes); Unstructured did not put it in the top 10 (Hit@5 no). The UI explorer lists **only DocLang**, **only Unstructured**, **both**, or **both missed**.

### Extra check (no embeddings)

We also ask: “Is the evidence text actually on that page after extraction?”  
That is **evidence coverage** (`extract.py` → `evidence_coverage`). It only checks word overlap. No OpenAI call.

### Setup (one time)

Unstructured runs in its own venv so its packages do not mix with the app:

```bash
cd backend
python3.12 -m venv .venv-unstructured
.venv-unstructured/bin/pip install -r requirements-unstructured.txt
```

Put your key in `backend/.env` (gitignored). The backend reads it; the browser never sees it.

```env
OPENAI_API_KEY=your-openai-api-key
RECALL_EMBEDDING_MODEL=text-embedding-3-large
RECALL_EMBEDDING_PRICE_PER_MILLION=0.13
```

**Local OpenSearch** (Rancher Desktop / Docker). From the repo root:

```bash
docker compose up -d
# https://localhost:9200  · Dashboards http://localhost:5601
# First export OPENSEARCH_INITIAL_ADMIN_PASSWORD (strong value of your choosing),
# then put the same value in backend/.env as OPENSEARCH_PASSWORD.
```

```env
OPENSEARCH_URL=https://localhost:9200
OPENSEARCH_USER=admin
OPENSEARCH_PASSWORD=
OPENSEARCH_VERIFY_CERTS=false
OPENSEARCH_INDEX_PREFIX=financebench-recall
```

### How to run it

UI buttons, in order:

1. **Estimate** — build chunks, count tokens, show embedding $ before you pay.
2. **Run comparison** — embed + score in memory.
3. **Ingest OpenSearch** — load vectors into local indexes.
4. **Score OpenSearch** — same questions, search with OpenSearch k-NN.

Or in a terminal:

```bash
cd backend
.venv/bin/python scripts/run_recall.py --estimate
.venv/bin/python scripts/run_recall.py
.venv/bin/python scripts/ingest_recall_opensearch.py
.venv/bin/python scripts/evaluate_opensearch.py --save
```

Results go to `backend/data/recall/runs/<run_id>.json` (gitignored).  
Open a run file and search for `financebench_id_03029` to see the question, evidence pages, and top chunks.

### When DocLang page numbers need a fix

Some `.dclx` files drop a PDF page and renumber the rest. FinanceBench still points at the **PDF** page. Without a fix, DocLang would look wrong only because of numbering.

We remap DocLang pages to the PDF using the PDF text layer (`align_to_pdf_pages` in `extract.py`). Unstructured reads the PDF, so it does not need this. Remapped docs are listed in the run JSON under `doclang_page_realignment`.

### How to read the scores

- A few points difference on 150 questions can be noise. Use the explorer, not only the big %.
- Both pipelines often miss the same hard questions.
- We only test Unstructured `fast`. Other Unstructured modes are not in this demo.
- Page `+ 1`, Hit@k, and top-k are covered in `backend/tests/test_recall.py`.

---

## Tests

```bash
cd backend
source .venv/bin/activate
pytest -q
```

The tests cover config parsing, file pairing, cache path safety, chunking (overlap, headings, tables), cost formulas, and loading a DocLang archive without PDF extraction. `tests/test_recall.py` covers the FinanceBench page mapping, per-page chunking, top-k retrieval against brute force, metric math, embedding-cache invalidation, and API-key scrubbing. None of the tests call OpenAI.

---

## Limitations

- No sample DocLang files are included. You need MinIO access or local `.dclx` copies.
- Full embedding runs download the Qwen model the first time and are slow on CPU.
- Page count needs a paired PDF, DocLang page data, or a manual value.
- The embedding $ is an estimate from token counts and your price, not a real bill.

---

## Quick mental model

**The PDF tells you how many pages the document has → extraction price.**  
**DocLang gives you the text to chunk and count → embedding price.**  
**When extraction dominates, keeping DocLang means later reingestions skip the expensive step.**
